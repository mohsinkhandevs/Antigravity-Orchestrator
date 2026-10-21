import pytest
from datetime import datetime, timedelta
from app.account_manager import AccountManager, GoogleAccount


def test_account_jump_feature_basic(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email=f"acc{i}@gmail.com", display_name=f"Acc {i}", enabled=True, quota_status="Ready")
        for i in range(1, 6)
    ]
    mgr.save_accounts()

    # Current account is acc2
    mgr.set_active_account("acc2@gmail.com")
    assert mgr.current_email == "acc2@gmail.com"

    # Normal circular rotation without jump should pick acc3
    normal_next = mgr.get_next_account("acc2@gmail.com")
    assert normal_next is not None
    assert normal_next.email == "acc3@gmail.com"

    # Set Jump Target to acc5 (skipping acc3 and acc4)
    ok = mgr.set_jump_next_account("acc5@gmail.com")
    assert ok is True
    assert mgr.jump_next_email == "acc5@gmail.com"

    # get_next_account should now return acc5
    jump_next = mgr.get_next_account("acc2@gmail.com")
    assert jump_next is not None
    assert jump_next.email == "acc5@gmail.com"

    # Simulate successful account switch to acc5
    mgr.set_active_account("acc5@gmail.com")
    assert mgr.current_email == "acc5@gmail.com"
    # Jump target must be consumed upon switch
    assert mgr.jump_next_email is None

    # Subsequent rotation must naturally resume circularly from acc5 (next is acc1)
    subsequent_next = mgr.get_next_account("acc5@gmail.com")
    assert subsequent_next is not None
    assert subsequent_next.email == "acc1@gmail.com"


def test_account_jump_to_frozen_target_falls_back(tmp_path, monkeypatch):
    from app.config import config
    monkeypatch.setattr(config, "min_gemini_five_hour_quota", 30)
    monkeypatch.setattr(config, "min_gemini_weekly_quota", 4)

    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email=f"acc{i}@gmail.com", display_name=f"Acc {i}", enabled=True, quota_status="Ready")
        for i in range(1, 6)
    ]
    mgr.save_accounts()
    mgr.set_active_account("acc2@gmail.com")

    # Freeze acc5 (low quota)
    now = datetime(2026, 10, 11, 12, 0, 0)
    mgr.update_account_quota("acc5@gmail.com", five_hour_percent=10, five_hour_refresh_raw="1 hour", now=now)
    assert mgr.get_account("acc5@gmail.com").is_frozen is True

    # Attempt to set Jump Target to frozen acc5
    mgr.set_jump_next_account("acc5@gmail.com")
    assert mgr.jump_next_email == "acc5@gmail.com"

    # get_next_account with consume_jump=True detects target is not available/frozen,
    # clears the override and falls back to normal circular next (acc3)
    target = mgr.get_next_account("acc2@gmail.com", now=now, consume_jump=True)
    assert target is not None
    assert target.email == "acc3@gmail.com"
    assert mgr.jump_next_email is None


def test_account_jump_clear(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email="acc1@gmail.com", display_name="Acc 1", enabled=True, quota_status="Ready"),
        GoogleAccount(email="acc2@gmail.com", display_name="Acc 2", enabled=True, quota_status="Ready"),
        GoogleAccount(email="acc3@gmail.com", display_name="Acc 3", enabled=True, quota_status="Ready"),
    ]
    mgr.save_accounts()
    mgr.set_active_account("acc1@gmail.com")

    mgr.set_jump_next_account("acc3@gmail.com")
    assert mgr.get_jump_next_account().email == "acc3@gmail.com"

    # Clear jump
    mgr.clear_jump_next_account()
    assert mgr.jump_next_email is None
    assert mgr.get_jump_next_account() is None
    assert mgr.get_next_account("acc1@gmail.com").email == "acc2@gmail.com"


def test_unregistered_account_does_not_save_to_json(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email="registered1@gmail.com", display_name="Reg 1", enabled=True, quota_status="Ready"),
        GoogleAccount(email="registered2@gmail.com", display_name="Reg 2", enabled=True, quota_status="Ready"),
    ]
    mgr.save_accounts()
    orig_mtime = f.stat().st_mtime_ns

    # Call set_active_account with an unregistered email
    unregistered_email = "unregistered.manual@gmail.com"
    mgr.set_active_account(unregistered_email)

    # In-memory tracking
    assert mgr.current_email == unregistered_email

    # Must NOT add to self.accounts
    assert len(mgr.accounts) == 2
    assert mgr.get_account(unregistered_email) is None

    # accounts.json on disk must NOT contain the unregistered email
    with open(f, "r", encoding="utf-8") as fp:
        raw_content = fp.read()
    assert unregistered_email not in raw_content


def test_unregistered_account_update_quota_does_not_add_account(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email="registered1@gmail.com", display_name="Reg 1", enabled=True, quota_status="Ready"),
    ]
    mgr.save_accounts()

    res = mgr.update_account_quota("unknown@domain.com", five_hour_percent=45, five_hour_refresh_raw="2h")
    assert res is None
    assert len(mgr.accounts) == 1
    assert mgr.get_account("unknown@domain.com") is None


def test_unregistered_account_rotation_does_not_use_index_math(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email="registered1@gmail.com", display_name="Reg 1", enabled=True, quota_status="Ready"),
        GoogleAccount(email="registered2@gmail.com", display_name="Reg 2", enabled=True, quota_status="Ready"),
        GoogleAccount(email="registered3@gmail.com", display_name="Reg 3", enabled=True, quota_status="Ready"),
    ]
    mgr.save_accounts()
    mgr.current_email = "external.unregistered@gmail.com"

    # Case 1: Unregistered account with NO jump target
    # Must NOT use curr_idx + offset; should cleanly return first available registered account
    next_acc = mgr.get_next_account("external.unregistered@gmail.com")
    assert next_acc is not None
    assert next_acc.email == "registered1@gmail.com"

    # Case 2: Unregistered account WITH jump target set to registered3
    mgr.set_jump_next_account("registered3@gmail.com")
    jump_next = mgr.get_next_account("external.unregistered@gmail.com")
    assert jump_next is not None
    assert jump_next.email == "registered3@gmail.com"


def test_get_next_account_with_exclude_emails(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email="acc1@gmail.com", display_name="Acc 1", enabled=True, quota_status="Ready"),
        GoogleAccount(email="acc2@gmail.com", display_name="Acc 2", enabled=True, quota_status="Ready"),
        GoogleAccount(email="acc3@gmail.com", display_name="Acc 3", enabled=True, quota_status="Ready"),
    ]
    mgr.save_accounts()

    # Normal rotation: acc1 -> acc2
    assert mgr.get_next_account("acc1@gmail.com").email == "acc2@gmail.com"

    # Excluding acc2: acc1 -> acc3
    skipped = mgr.get_next_account("acc1@gmail.com", exclude_emails=["acc2@gmail.com"])
    assert skipped is not None
    assert skipped.email == "acc3@gmail.com"

    # Excluding all remaining: returns None
    none_left = mgr.get_next_account("acc1@gmail.com", exclude_emails=["acc1@gmail.com", "acc2@gmail.com", "acc3@gmail.com"])
    assert none_left is None


def test_mark_account_auth_required(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email="normal@gmail.com", display_name="Normal", enabled=True, quota_status="Ready"),
        GoogleAccount(email="blocked@gmail.com", display_name="Blocked", enabled=True, quota_status="Ready"),
    ]
    mgr.save_accounts()

    # Mark as requiring 2FA
    mgr.mark_account_auth_required("blocked@gmail.com", reason="2FA Device Challenge")

    # Verify quota_status updated
    acc = mgr.get_account("blocked@gmail.com")
    assert acc.quota_status == "Auth Required"
    assert "2FA Device Challenge" in acc.last_quota_reason

    # Verify excluded from available accounts
    available = mgr.get_available_accounts()
    assert len(available) == 1
    assert available[0].email == "normal@gmail.com"

