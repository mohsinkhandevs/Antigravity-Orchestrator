"""Account switching worker thread for Antigravity CDP."""

import time
import threading
from typing import Optional, Any
from PyQt6.QtCore import QThread, pyqtSignal
from cdp.client import AntigravityCDPClient
from cdp.quota_inspector import DOMQuotaInspector
from cdp.browser_oauth import BrowserOAuthHandler
from app.account_manager import account_manager
from app.config import config


class AccountSwitchWorker(QThread):
    sig_log = pyqtSignal(str, str)
    sig_step = pyqtSignal(str, str)
    sig_step_changed = pyqtSignal(str, str)
    sig_progress = pyqtSignal(int, int)
    sig_quota_updated = pyqtSignal(dict)
    sig_finished = pyqtSignal(bool, str, str)
    sig_pre_switch_countdown_started = pyqtSignal(str, str, int)
    sig_pre_switch_countdown_tick = pyqtSignal(int, int)
    sig_pre_switch_countdown_finished = pyqtSignal()

    def __init__(
        self,
        target_hwnd: int = 0,
        target_account_email: Optional[str] = None,
        is_manual_mode: bool = False,
        countdown_seconds: int = 0,
        force_switch: bool = True,
        parent: Optional[Any] = None,
        **kwargs,
    ):
        super().__init__(parent)
        self.target_hwnd = target_hwnd
        self.target_account_email = target_account_email
        self.is_manual_mode = is_manual_mode
        self.countdown_seconds = countdown_seconds
        self.force_switch = force_switch
        self._stop_requested = threading.Event()
        self._switch_now_event = threading.Event()

    def cancel(self):
        self._stop_requested.set()
        self._switch_now_event.set()

    def trigger_switch_now(self):
        """Triggered when user clicks 'Change Now' on the 30s Pre-Switch Mini HUD."""
        self._switch_now_event.set()

    def _wait_for_pre_switch_countdown(self, current_email: str, target_email: str) -> bool:
        if not getattr(config, "enable_pre_switch_hud", True):
            return not self._stop_requested.is_set()

        total_sec = int(getattr(config, "pre_switch_countdown_seconds", 30))
        if total_sec <= 0:
            return not self._stop_requested.is_set()

        self._switch_now_event.clear()
        self.sig_pre_switch_countdown_started.emit(current_email or "", target_email or "", total_sec)
        self.sig_log.emit(
            "INFO",
            f"⏳ Waiting {total_sec}s before clicking 'Continue with Google' for '{target_email}' "
            f"(click 'Change Now' on Mini HUD to switch immediately)...",
        )

        deadline = time.time() + total_sec
        last_emitted_sec = total_sec
        try:
            while not self._stop_requested.is_set():
                if self._switch_now_event.is_set():
                    self.sig_log.emit(
                        "INFO",
                        "⚡ 'Change Now' clicked on Mini HUD — switching Google account immediately!",
                    )
                    return True

                remaining = deadline - time.time()
                if remaining <= 0:
                    self.sig_log.emit(
                        "INFO",
                        f"⏰ {total_sec}s pre-switch countdown elapsed — automatically clicking 'Continue with Google'...",
                    )
                    return True

                rem_int = int(remaining) + (1 if (remaining - int(remaining)) > 0.05 else 0)
                rem_int = max(0, min(total_sec, rem_int))
                if rem_int != last_emitted_sec:
                    last_emitted_sec = rem_int
                    self.sig_pre_switch_countdown_tick.emit(rem_int, total_sec)
                    self._emit_step(
                        5,
                        10,
                        "Pre-Switch Timer",
                        f"Switching to {target_email} in {rem_int}s (or click Change Now)...",
                    )

                self._switch_now_event.wait(timeout=0.1)
            return False
        finally:
            self.sig_pre_switch_countdown_finished.emit()

    def _emit_step(self, step_num: int, total: int, title: str, desc: str):
        label = f"Step {step_num}/{total}: {title}"
        self.sig_step.emit(label, desc)
        self.sig_step_changed.emit(label, desc)
        self.sig_progress.emit(step_num, total)

    def run(self):
        total_steps = 10
        client = None
        try:
            self._emit_step(1, total_steps, "Connect CDP", "Connecting to Antigravity CDP...")
            client = AntigravityCDPClient()
            ok, msg = client.connect()
            if not ok:
                self.sig_finished.emit(False, "", msg)
                return

            if self._stop_requested.is_set():
                self.sig_finished.emit(False, "", "Cancelled by user.")
                return

            page = client.get_page()

            # Step 2: Pre-flight quota check if not forced, or to update quota metrics
            from cdp.state_manager import AntigravityStateManager, AntigravityUIState
            state_mgr = AntigravityStateManager(page)
            is_already_logged_out = (state_mgr.detect_state() == AntigravityUIState.LOGGED_OUT)

            if not is_already_logged_out:
                self._emit_step(2, total_steps, "Inspect Quota", "Checking Gemini model quota...")
                inspector = DOMQuotaInspector(page)
                q_res = inspector.inspect_model_quotas(
                    close_settings=False,
                    log_callback=lambda lvl, m: self.sig_log.emit(lvl, m),
                )
                self.sig_quota_updated.emit(q_res)
                gem = q_res.get("gemini", {})
                detected_pre = (q_res.get("current_email") or "").strip().lower()
                if detected_pre:
                    account_manager.set_active_account(detected_pre)
                    curr = detected_pre
                else:
                    curr = account_manager.current_email

                if curr and (gem.get("five_hour_percent") is not None or gem.get("weekly_percent") is not None):
                    account_manager.update_account_quota(
                        email=curr,
                        five_hour_percent=gem.get("five_hour_percent"),
                        five_hour_refresh_raw=gem.get("five_hour_refresh", ""),
                        weekly_percent=gem.get("weekly_percent"),
                        weekly_refresh_raw=gem.get("weekly_refresh", ""),
                    )

                if not self.force_switch and q_res.get("is_safe_to_prompt", False):
                    page.keyboard.press("Escape")
                    self.sig_progress.emit(total_steps, total_steps)
                    self.sig_finished.emit(True, "", "")
                    return
            else:
                self.sig_log.emit("INFO", "Antigravity is already logged out. Proceeding directly to Google sign-in...")
                self._emit_step(2, total_steps, "Detect State", "Account is logged out. Preparing Google sign-in...")

            if self._stop_requested.is_set():
                self.sig_finished.emit(False, "", "Cancelled by user.")
                return

            # Steps 3..9: Execute account switch via CDP & Browser OCR
            handler = BrowserOAuthHandler(page)

            def on_step_update(s_id: int, succ: bool, detail: str):
                self._emit_step(s_id, total_steps, f"Step {s_id}", detail)

            res = handler.execute_account_switch(
                target_account_email=self.target_account_email,
                log_callback=lambda lvl, m: self.sig_log.emit(lvl, m),
                step_callback=on_step_update,
                stop_event=self._stop_requested,
                pre_continue_callback=self._wait_for_pre_switch_countdown,
            )
            if res.get("success"):
                # Step 10: Verify fresh quota for new account
                self._emit_step(10, total_steps, "Verify Quota", "Verifying fresh Gemini quota for new account...")
                q_inspector = DOMQuotaInspector(page)
                post_q = q_inspector.inspect_model_quotas(
                    close_settings=True,
                    log_callback=lambda lvl, m: self.sig_log.emit(lvl, m),
                )
                post_q["check_reason"] = "Account switch verification"
                self.sig_quota_updated.emit(post_q)
                post_gem = post_q.get("gemini", {})
                detected_post = (post_q.get("current_email") or "").strip().lower()
                switched_email = detected_post or res.get("switched_to_email") or account_manager.current_email
                if switched_email:
                    account_manager.set_active_account(switched_email)
                    if post_gem.get("five_hour_percent") is not None or post_gem.get("weekly_percent") is not None:
                        account_manager.update_account_quota(
                            email=switched_email,
                            five_hour_percent=post_gem.get("five_hour_percent"),
                            five_hour_refresh_raw=post_gem.get("five_hour_refresh", ""),
                            weekly_percent=post_gem.get("weekly_percent"),
                            weekly_refresh_raw=post_gem.get("weekly_refresh", ""),
                            reason="Account switch verification",
                        )
                # Ensure workspace state is fully normalized (dismiss post-auth terms, settings, error modals)
                try:
                    from cdp.state_manager import AntigravityStateManager
                    state_mgr = AntigravityStateManager(page)
                    state_mgr.normalize_to_workspace(
                        log_callback=lambda lvl, m: self.sig_log.emit(lvl, m),
                        oauth_handler=handler,
                        stop_event=self._stop_requested,
                    )
                except Exception:
                    pass

                self.sig_progress.emit(total_steps, total_steps)
                self.sig_finished.emit(True, res.get("switched_to_email", ""), "")
            else:
                self.sig_finished.emit(False, "", res.get("error", "Account switch failed."))
        except Exception as e:
            self.sig_finished.emit(False, "", str(e))
        finally:
            if client:
                try:
                    client.disconnect()
                except Exception:
                    pass
