import os
import json
import uuid
import re
import difflib
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.config import get_user_data_dir


FREEZE_WEEKLY_THRESHOLD = 3  # %

@dataclass
class GoogleAccount:
    email: str
    display_name: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    enabled: bool = True
    last_used: str = ""
    quota_status: str = "Ready"  # 'Ready', 'Active', 'Low Quota', 'Exhausted', 'Frozen'

    # 5-Hour Quota & Refresh Tracking (Actual minutes + 1 buffer)
    five_hour_percent: Optional[int] = None       # e.g., 86 (% remaining)
    five_hour_refresh_raw: str = ""               # e.g., "4 hours, 4 minutes"
    five_hour_reset_at: str = ""                  # e.g., "2026-10-10 20:45:00"

    # Weekly Quota & Refresh Tracking (Actual hours + 1 buffer)
    weekly_percent: Optional[int] = None          # e.g., 97 (% remaining)
    weekly_refresh_raw: str = ""                  # e.g., "6 days, 23 hours"
    weekly_reset_at: str = ""                     # e.g., "2026-10-17 19:40:00"

    is_frozen: bool = False                       # True if five_hour or weekly < threshold and now < reset_at
    last_quota_checked: str = ""                  # e.g., "2026-10-10 20:39:10"
    last_quota_reason: str = ""                   # e.g., "Pre-dispatch check (Project A)", "Manual on-demand check"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoogleAccount":
        return cls(
            email=data.get("email", "").strip(),
            display_name=data.get("display_name", "").strip(),
            id=data.get("id", str(uuid.uuid4())[:8]),
            enabled=bool(data.get("enabled", True)),
            last_used=data.get("last_used", ""),
            quota_status=data.get("quota_status", "Ready"),
            five_hour_percent=data.get("five_hour_percent"),
            five_hour_refresh_raw=data.get("five_hour_refresh_raw", ""),
            five_hour_reset_at=data.get("five_hour_reset_at", ""),
            weekly_percent=data.get("weekly_percent"),
            weekly_refresh_raw=data.get("weekly_refresh_raw", ""),
            weekly_reset_at=data.get("weekly_reset_at", ""),
            is_frozen=bool(data.get("is_frozen", False)),
            last_quota_checked=data.get("last_quota_checked", ""),
            last_quota_reason=data.get("last_quota_reason", ""),
        )


class AccountManager:
    """
    Manages the user's series of Google accounts in a persistent circular queue.
    Supports enable/disable toggling, priority re-ordering, weekly quota tracking,
    freezing when weekly quota is below 3%, and automated unfreezing upon reset.
    """

    FREEZE_WEEKLY_THRESHOLD = 3  # %

    def __init__(self, filepath: Optional[str] = None):
        self.filepath = filepath or os.path.join(get_user_data_dir(), "accounts.json")
        self.accounts: List[GoogleAccount] = []
        self.current_email: str = ""
        self.jump_next_email: Optional[str] = None
        self.unregistered_quota: Optional[Dict[str, Any]] = None
        self.load_accounts()

    def set_jump_next_account(self, email: str) -> bool:
        """Sets an account as the manual next jump target if it exists and is enabled."""
        clean = (email or "").strip().lower()
        acc = self.get_account(clean)
        if acc and acc.enabled:
            self.jump_next_email = acc.email
            return True
        return False

    def clear_jump_next_account(self):
        """Clears the manual jump target override."""
        self.jump_next_email = None

    def get_jump_next_account(self) -> Optional[GoogleAccount]:
        """Returns the active jump target account if valid."""
        if not self.jump_next_email:
            return None
        return self.get_account(self.jump_next_email)

    def get_accounts_file_path(self) -> str:
        return self.filepath

    def _is_production_file(self) -> bool:
        """Determines if this manager points to the real user production accounts.json."""
        try:
            prod_path = os.path.abspath(os.path.join(get_user_data_dir(), "accounts.json"))
            return os.path.abspath(self.filepath) == prod_path
        except Exception:
            return False

    def _get_backup_file_path(self) -> str:
        """Returns the redundant backup file path for accounts."""
        dir_name = os.path.dirname(self.filepath) or get_user_data_dir()
        return os.path.join(dir_name, "accounts.backup.json")

    def load_accounts(self):
        """Loads accounts from JSON file, falling back to backup or user seed accounts."""
        data = None

        # Priority 1: Main accounts.json
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
            except Exception as e:
                print(f"[WARN] Failed to load {self.filepath}: {e}")

        # Priority 2: Auto-recover from accounts.backup.json if main file is missing/empty/corrupted
        if data is None:
            backup_path = self._get_backup_file_path()
            if os.path.exists(backup_path):
                try:
                    with open(backup_path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            data = json.loads(content)
                            print(f"[INFO] Recovered accounts list from backup: {backup_path}")
                            # Auto-restore main accounts.json from backup
                            try:
                                shutil.copy2(backup_path, self.filepath)
                            except Exception:
                                pass
                except Exception as be:
                    print(f"[WARN] Failed to load backup accounts file: {be}")

        if isinstance(data, list):
            self.accounts = [GoogleAccount.from_dict(item) for item in data if item.get("email")]
            # Check and process auto-unfreezing for accounts whose reset time passed
            for acc in self.accounts:
                self.is_account_frozen(acc)
            active_acc = next((a for a in self.accounts if a.quota_status == "Active"), None)
            if active_acc:
                self.current_email = active_acc.email
            elif self.accounts:
                self.current_email = self.accounts[0].email
            else:
                self.current_email = ""
            return

        # Priority 3: Fallback seed accounts (clean default without hardcoded personal accounts)
        self.accounts = []
        self.current_email = ""
        # Only persist seed if not running automated tests and accounts are present
        if self.accounts and not ("PYTEST_CURRENT_TEST" in os.environ and self._is_production_file()):
            self.save_accounts()

    def save_accounts(self, allow_empty: bool = False):
        """Persists accounts to JSON with atomic write, backup mirroring, and test-safety guard."""
        # 1. Safety Guard: Never overwrite production accounts.json during pytest/test execution!
        if ("PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ) and self._is_production_file():
            return

        try:
            # 2. Prepare payload
            payload = [acc.to_dict() for acc in self.accounts]
            if not payload and not allow_empty and os.path.exists(self.filepath):
                # Guard against accidentally wiping an existing file with an empty list
                return

            # 3. Create/update backup mirror if target file exists and is populated
            if os.path.exists(self.filepath) and os.path.getsize(self.filepath) > 10:
                backup_path = self._get_backup_file_path()
                try:
                    shutil.copy2(self.filepath, backup_path)
                except Exception:
                    pass

            # 4. Atomic write via temp file to prevent partial write or file corruption
            dir_name = os.path.dirname(self.filepath) or get_user_data_dir()
            tmp_path = os.path.join(dir_name, f".accounts_{os.getpid()}_{uuid.uuid4().hex[:6]}.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            os.replace(tmp_path, self.filepath)

            # 5. Mirror to backup if allow_empty and not payload
            if allow_empty and not payload:
                backup_path = self._get_backup_file_path()
                try:
                    shutil.copy2(self.filepath, backup_path)
                except Exception:
                    pass

        except Exception as e:
            print(f"[ERROR] Failed to save accounts.json: {e}")

    def _get_min_five_hour_threshold(self) -> int:
        try:
            from app.config import config
            return getattr(config, "min_gemini_five_hour_quota", 30)
        except Exception:
            return 30

    def _get_min_weekly_threshold(self) -> int:
        try:
            from app.config import config
            return getattr(config, "min_gemini_weekly_quota", 4)
        except Exception:
            return 4

    def is_account_frozen(self, acc: GoogleAccount, now: Optional[datetime] = None) -> bool:
        """
        Checks whether an account is currently frozen / unusable.
        An account is ONLY unusable if either its 5-hour limit or weekly limit is below threshold
        AND its respective reset timestamp has NOT yet passed.
        If all expired reset times have elapsed, the account is AUTOMATICALLY unfrozen.
        """
        curr = now or datetime.now()
        min_five = self._get_min_five_hour_threshold()
        min_week = self._get_min_weekly_threshold()

        five_hour_still_frozen = False
        if acc.five_hour_percent is not None and acc.five_hour_percent < min_five:
            if acc.five_hour_reset_at:
                try:
                    five_reset_dt = datetime.strptime(acc.five_hour_reset_at, "%Y-%m-%d %H:%M:%S")
                    if curr < five_reset_dt:
                        five_hour_still_frozen = True
                except Exception:
                    pass
            else:
                # No reset time recorded yet; preserve freeze if explicitly frozen
                if acc.is_frozen:
                    five_hour_still_frozen = True

        weekly_still_frozen = False
        if acc.weekly_percent is not None and acc.weekly_percent < min_week:
            if acc.weekly_reset_at:
                try:
                    week_reset_dt = datetime.strptime(acc.weekly_reset_at, "%Y-%m-%d %H:%M:%S")
                    if curr < week_reset_dt:
                        weekly_still_frozen = True
                except Exception:
                    pass
            else:
                if acc.is_frozen:
                    weekly_still_frozen = True

        is_still_frozen = (five_hour_still_frozen or weekly_still_frozen)

        if not is_still_frozen and (acc.is_frozen or acc.quota_status == "Frozen"):
            # Auto-unfreeze: reset time has passed or limits are above thresholds!
            acc.is_frozen = False
            if acc.quota_status in ("Frozen", "Low Quota"):
                acc.quota_status = "Active" if acc.email.lower() == (self.current_email or "").lower() else "Ready"
            self.save_accounts()
            return False

        return is_still_frozen

    def get_enabled_accounts(self) -> List[GoogleAccount]:
        """Returns list of all accounts enabled for automatic switching (including frozen)."""
        return [acc for acc in self.accounts if acc.enabled]

    def get_available_accounts(self, now: Optional[datetime] = None) -> List[GoogleAccount]:
        """
        Returns list of all enabled accounts that are NOT currently frozen and do not have 'Auth Required'.
        Automatically checks and unfreezes accounts whose reset timestamp has arrived.
        """
        return [
            acc for acc in self.accounts
            if acc.enabled and acc.quota_status != "Auth Required" and not self.is_account_frozen(acc, now)
        ]

    def get_next_account(
        self,
        current_email: Optional[str] = None,
        now: Optional[datetime] = None,
        consume_jump: bool = False,
        exclude_emails: Optional[List[str]] = None,
    ) -> Optional[GoogleAccount]:
        """
        Circular queue selector with manual jump override and safe unknown-account handling:
        1. If manual jump target (self.jump_next_email) is set and valid/unfrozen:
           Returns the jump target (and optionally consumes it if consume_jump=True).
        2. If current_email is an unregistered / unknown account (curr_idx == -1):
           Does NOT use curr_idx + offset math. Returns first available registered account.
        3. If current_email is registered (curr_idx != -1):
           Circularly searches for next available account using (curr_idx + offset) % len.
        """
        available = self.get_available_accounts(now=now)
        if exclude_emails:
            excluded_set = {e.lower().strip() for e in exclude_emails if e}
            available = [acc for acc in available if acc.email.lower() not in excluded_set]
        if not available:
            return None

        # Priority 1: Check manual jump target override
        if self.jump_next_email:
            jump_acc = self.get_account(self.jump_next_email)
            if jump_acc and jump_acc in available:
                if consume_jump:
                    self.jump_next_email = None
                return jump_acc
            elif consume_jump:
                # Target is disabled or frozen; clear invalid override
                self.jump_next_email = None

        lookup_email = (current_email or self.current_email or "").strip().lower()

        # Find position of current email in the full accounts sequence
        curr_idx = -1
        for idx, acc in enumerate(self.accounts):
            if acc.email.lower() == lookup_email:
                curr_idx = idx
                break

        # Priority 2: Registered account circular search
        if curr_idx != -1 and len(self.accounts) > 1:
            for offset in range(1, len(self.accounts)):
                candidate = self.accounts[(curr_idx + offset) % len(self.accounts)]
                if candidate in available:
                    return candidate

        # Priority 3: Unknown / unregistered outside account (or single account in pool)
        # For outside accounts (curr_idx == -1), return first available/ready registered account.
        return available[0]

    def update_account_quota(
        self,
        email: str,
        five_hour_percent: Optional[int] = None,
        five_hour_refresh_raw: str = "",
        weekly_percent: Optional[int] = None,
        weekly_refresh_raw: str = "",
        now: Optional[datetime] = None,
        reason: str = "",
    ) -> Optional[GoogleAccount]:
        """
        Updates both 5-Hour and Weekly quota metrics for an account.
        Calculates:
        - 5-Hour reset timestamp with: (actual minutes + 1) buffer.
        - Weekly reset timestamp with: (actual hours + 1) buffer.
        Applies strict usability / freeze policy:
        - Frozen if: five_hour_percent < min_five OR weekly_percent < min_week.
        - Ready / Active if: both limits >= thresholds.
        """
        clean_email = (email or "").strip().lower()
        if not clean_email or "@" not in clean_email:
            return None

        target_acc = None
        # Strict exact email match only (Never fuzzy match or prefix match outside accounts into registered pool)
        for acc in self.accounts:
            if acc.email.lower() == clean_email:
                target_acc = acc
                break

        if not target_acc:
            # Unregistered account: do NOT auto-add or persist to accounts.json!
            # Instead, record its live quota strictly in-memory so UI cards can display it accurately.
            curr_time = now or datetime.now()
            reset_5h_str = ""
            if five_hour_refresh_raw and five_hour_refresh_raw.lower() not in ("unknown", "n/a", ""):
                try:
                    from app.quota_checker import calculate_five_hour_reset_timestamp
                    reset_5h = calculate_five_hour_reset_timestamp(five_hour_refresh_raw, base_time=curr_time)
                    if reset_5h:
                        reset_5h_str = reset_5h.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            reset_wk_str = ""
            if weekly_refresh_raw and weekly_refresh_raw.lower() not in ("unknown", "n/a", ""):
                try:
                    from app.quota_checker import calculate_weekly_reset_timestamp
                    reset_wk = calculate_weekly_reset_timestamp(weekly_refresh_raw, base_time=curr_time)
                    if reset_wk:
                        reset_wk_str = reset_wk.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            self.unregistered_quota = {
                "email": clean_email,
                "five_hour_percent": five_hour_percent,
                "five_hour_refresh_raw": five_hour_refresh_raw,
                "five_hour_reset_at": reset_5h_str,
                "weekly_percent": weekly_percent,
                "weekly_refresh_raw": weekly_refresh_raw,
                "weekly_reset_at": reset_wk_str,
                "last_quota_checked": curr_time.strftime("%Y-%m-%d %H:%M:%S"),
                "last_quota_reason": reason or "Live model limit inspection",
            }
            # Return None to indicate not in registered accounts pool
            return None

        curr_time = now or datetime.now()
        target_acc.last_quota_checked = curr_time.strftime("%Y-%m-%d %H:%M:%S")
        if reason:
            target_acc.last_quota_reason = reason
        elif not target_acc.last_quota_reason:
            target_acc.last_quota_reason = "Live model limit inspection"

        # 1. Update 5-Hour Quota & Calculate Reset (minutes + 1)
        if five_hour_percent is not None:
            target_acc.five_hour_percent = five_hour_percent

        if five_hour_refresh_raw and five_hour_refresh_raw.lower() not in ("unknown", "n/a", ""):
            target_acc.five_hour_refresh_raw = five_hour_refresh_raw
            try:
                from app.quota_checker import calculate_five_hour_reset_timestamp
                reset_5h = calculate_five_hour_reset_timestamp(five_hour_refresh_raw, base_time=curr_time)
                if reset_5h:
                    target_acc.five_hour_reset_at = reset_5h.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        # 2. Update Weekly Quota & Calculate Reset (hours + 1)
        if weekly_percent is not None:
            target_acc.weekly_percent = weekly_percent

        if weekly_refresh_raw and weekly_refresh_raw.lower() not in ("unknown", "n/a", ""):
            target_acc.weekly_refresh_raw = weekly_refresh_raw
            try:
                from app.quota_checker import calculate_weekly_reset_timestamp
                reset_wk = calculate_weekly_reset_timestamp(weekly_refresh_raw, base_time=curr_time)
                if reset_wk:
                    target_acc.weekly_reset_at = reset_wk.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        # 3. Apply Strict Usability & Freeze Policy
        min_five = self._get_min_five_hour_threshold()
        min_week = self._get_min_weekly_threshold()

        is_low_five = (target_acc.five_hour_percent is not None and target_acc.five_hour_percent < min_five)
        is_low_week = (target_acc.weekly_percent is not None and target_acc.weekly_percent < min_week)

        if is_low_five or is_low_week:
            target_acc.is_frozen = True
            target_acc.quota_status = "Frozen"
        else:
            target_acc.is_frozen = False
            if target_acc.quota_status in ("Frozen", "Low Quota"):
                is_current = (target_acc.email.lower() == (self.current_email or "").lower())
                target_acc.quota_status = "Active" if is_current else "Ready"

        self.save_accounts()
        return target_acc

    def get_unregistered_quota(self, email: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Returns in-memory quota metrics for an unregistered account if available."""
        if not self.unregistered_quota:
            return None
        if email:
            clean = email.split("(")[0].strip().lower()
            if self.unregistered_quota.get("email", "").lower() != clean:
                return None
        return self.unregistered_quota

    def get_account(self, email_or_id: str) -> Optional[GoogleAccount]:
        """Looks up an account by email or id."""
        key = (email_or_id or "").strip().lower()
        for acc in self.accounts:
            if acc.email.lower() == key or acc.id == key:
                return acc
        return None

    def is_registered_account(self, email: Optional[str] = None) -> bool:
        """Returns True if the email belongs to a registered account in self.accounts."""
        lookup = (email or self.current_email or "").split("(")[0].strip().lower()
        if not lookup:
            return False
        return any(acc.email.lower() == lookup for acc in self.accounts)

    def mark_account_used(self, email: str, quota_status: str = "Active"):
        """Updates last_used timestamp and status for an account."""
        if not email:
            return
        clean_email = email.split("(")[0].strip().lower()
        if not clean_email:
            return
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.current_email = clean_email
        found = False
        for acc in self.accounts:
            if acc.email.lower() == clean_email:
                acc.last_used = now_str
                if not self.is_account_frozen(acc):
                    acc.quota_status = quota_status
                found = True
            elif acc.quota_status == "Active" and quota_status == "Active":
                acc.quota_status = "Ready" if not self.is_account_frozen(acc) else "Frozen"

        # Consume jump target if switching to it
        if self.jump_next_email and self.jump_next_email.lower() == clean_email:
            self.jump_next_email = None

        if found:
            self.save_accounts()

    def set_active_account(self, email: str):
        """Alias for mark_account_used with Active status."""
        self.mark_account_used(email, quota_status="Active")

    def mark_account_auth_required(self, email: str, reason: str = "2FA Challenge Required"):
        """Marks an account as requiring authentication / 2FA intervention and flags it."""
        if not email:
            return
        clean_email = email.split("(")[0].strip().lower()
        acc = self.get_account(clean_email)
        if acc:
            acc.quota_status = "Auth Required"
            acc.last_quota_reason = reason
            self.save_accounts()



    def add_account(self, email: str, display_name: str = "") -> Optional[GoogleAccount]:
        """Adds a new account if not already in list."""
        clean_email = email.strip().lower()
        if not clean_email or "@" not in clean_email:
            return None

        for acc in self.accounts:
            if acc.email.lower() == clean_email:
                # Already exists; update name and ensure enabled
                acc.display_name = display_name.strip() or acc.display_name
                self.save_accounts()
                return acc

        new_acc = GoogleAccount(
            email=clean_email,
            display_name=display_name.strip(),
            enabled=True,
            quota_status="Ready",
        )
        self.accounts.append(new_acc)
        self.save_accounts()
        return new_acc

    def remove_account(self, email_or_id: str) -> bool:
        """Removes an account by email or id."""
        key = email_or_id.strip().lower()
        initial_len = len(self.accounts)
        self.accounts = [a for a in self.accounts if a.email.lower() != key and a.id != key]
        if len(self.accounts) < initial_len:
            self.save_accounts(allow_empty=True)
            return True
        return False

    def toggle_account(self, email_or_id: str, enabled: bool) -> bool:
        """Enables or disables an account."""
        key = email_or_id.strip().lower()
        for a in self.accounts:
            if a.email.lower() == key or a.id == key:
                a.enabled = enabled
                self.save_accounts()
                return True
        return False

    def move_account(self, target: Any, direction: Any) -> bool:
        """
        Re-orders account up or down in the queue sequence.
        target can be row index (int), account id (str), or email (str).
        direction can be -1 / 1 (int) or 'up' / 'down' (str).
        """
        idx = -1
        if isinstance(target, int):
            if 0 <= target < len(self.accounts):
                idx = target
        elif isinstance(target, str):
            key = target.strip().lower()
            for i, a in enumerate(self.accounts):
                if a.email.lower() == key or a.id == key:
                    idx = i
                    break

        if idx == -1:
            return False

        if isinstance(direction, int):
            new_idx = idx + direction
        else:
            new_idx = idx - 1 if str(direction).lower() == "up" else idx + 1

        if 0 <= new_idx < len(self.accounts):
            self.accounts[idx], self.accounts[new_idx] = self.accounts[new_idx], self.accounts[idx]
            self.save_accounts()
            return True
        return False


# Singleton instance
account_manager = AccountManager()
