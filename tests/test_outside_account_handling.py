import pytest
from datetime import datetime
from unittest.mock import MagicMock
from app.account_manager import AccountManager, GoogleAccount
from app.config import config


def test_outside_account_exact_match_isolation(tmp_path):
    """
    Verifies that an outside account (e.g. outside513786@gmail.com)
    is NOT falsely matched to a similarly named queue account (e.g. testuser786@gmail.com)
    and does NOT corrupt or freeze the queue account.
    """
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(
            email="testuser786@gmail.com",
            display_name="Test User 1",
            enabled=True,
            quota_status="Ready",
            five_hour_percent=50,
            weekly_percent=80,
            is_frozen=False,
        ),
        GoogleAccount(
            email="testuser785@gmail.com",
            display_name="Test User 2",
            enabled=True,
            quota_status="Ready",
            five_hour_percent=60,
            weekly_percent=90,
            is_frozen=False,
        ),
    ]
    mgr.save_accounts()

    outside_email = "outside513786@gmail.com"

    # 1. is_registered_account must return False
    assert mgr.is_registered_account(outside_email) is False
    assert mgr.is_registered_account("testuser786@gmail.com") is True

    # 2. update_account_quota with low quota (e.g. 15% < threshold) for the outside email
    res = mgr.update_account_quota(
        email=outside_email,
        five_hour_percent=15,
        five_hour_refresh_raw="4 hours",
        weekly_percent=70,
        weekly_refresh_raw="6 days",
        reason="Test pre-dispatch check",
    )
    # Must return None (unregistered account)
    assert res is None

    # Queue account #1 must remain completely unaffected (NOT frozen, NOT 15%)
    acc1 = mgr.get_account("testuser786@gmail.com")
    assert acc1.quota_status == "Ready"
    assert acc1.is_frozen is False
    assert acc1.five_hour_percent == 50

    # In-memory unregistered quota must be stored accurately
    unreg_q = mgr.get_unregistered_quota(outside_email)
    assert unreg_q is not None
    assert unreg_q["email"] == outside_email
    assert unreg_q["five_hour_percent"] == 15


def test_outside_account_target_resolution(tmp_path):
    """
    Verifies that when an outside account is active:
    - Default target is the first ready registered account.
    - If user selected a jump target, target is the jump target.
    """
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(
            email="queue1@gmail.com",
            display_name="Q1",
            enabled=True,
            quota_status="Ready",
            is_frozen=False,
        ),
        GoogleAccount(
            email="queue2@gmail.com",
            display_name="Q2",
            enabled=True,
            quota_status="Ready",
            is_frozen=False,
        ),
    ]
    mgr.save_accounts()

    outside_email = "personal.outside@gmail.com"
    mgr.set_active_account(outside_email)
    assert mgr.current_email == outside_email

    # Rule 1: No jump target -> returns first ready account (queue1)
    target = mgr.get_next_account(outside_email)
    assert target is not None
    assert target.email == "queue1@gmail.com"

    # Rule 2: User sets jump target to queue2 -> returns queue2
    mgr.set_jump_next_account("queue2@gmail.com")
    jump_target = mgr.get_next_account(outside_email)
    assert jump_target is not None
    assert jump_target.email == "queue2@gmail.com"


def test_worker_pre_dispatch_allows_healthy_outside_account():
    """
    Verifies that worker._check_pre_dispatch_quota permits prompt execution
    on an active outside account as long as its quota is above safety thresholds.
    """
    from app.worker import AutomationWorker
    from app.account_manager import account_manager

    orig_accounts = list(account_manager.accounts)
    orig_email = account_manager.current_email

    try:
        account_manager.accounts = [
            GoogleAccount(
                email="queue1@gmail.com",
                display_name="Q1",
                enabled=True,
                quota_status="Ready",
                is_frozen=False,
            ),
        ]
        outside_email = "outside.user@gmail.com"
        account_manager.current_email = outside_email

        worker = AutomationWorker(queue=[])
        worker.quota_inspector = MagicMock()
        # Outside account with healthy quota (80% 5-hour, 90% weekly)
        worker.quota_inspector.inspect_model_quotas.return_value = {
            "success": True,
            "is_safe_to_prompt": True,
            "current_email": outside_email,
            "gemini": {
                "five_hour_percent": 80,
                "five_hour_refresh": "4 hours",
                "weekly_percent": 90,
                "weekly_refresh": "6 days",
            },
        }

        enforce_called = []
        def fake_enforce_quota(label, known_unsafe_res=None):
            enforce_called.append((label, known_unsafe_res))
            return True

        worker._enforce_quota = fake_enforce_quota

        # Run check
        ok = worker._check_pre_dispatch_quota("TestProject")
        # Must return True directly, allowing prompt execution!
        assert ok is True
        # Must NOT trigger account rotation while quota is safe
        assert len(enforce_called) == 0

        # Cached check within TTL must also return True immediately
        cached_ok = worker._check_pre_dispatch_quota("TestProject")
        assert cached_ok is True
        # Quota inspector was not called a second time
        assert worker.quota_inspector.inspect_model_quotas.call_count == 1
    finally:
        account_manager.accounts = orig_accounts
        account_manager.current_email = orig_email


def test_worker_pre_dispatch_rotates_when_outside_account_depleted():
    """
    Verifies that worker._check_pre_dispatch_quota detects when an outside account's
    quota drops below threshold, flags unsafe, and triggers rotation to the target queue account.
    """
    from app.worker import AutomationWorker
    from app.account_manager import account_manager

    orig_accounts = list(account_manager.accounts)
    orig_email = account_manager.current_email

    try:
        account_manager.accounts = [
            GoogleAccount(
                email="queue1@gmail.com",
                display_name="Q1",
                enabled=True,
                quota_status="Ready",
                is_frozen=False,
            ),
        ]
        outside_email = "outside.user@gmail.com"
        account_manager.current_email = outside_email

        worker = AutomationWorker(queue=[])
        worker.quota_inspector = MagicMock()
        # Outside account with low quota (15% 5-hour < threshold)
        worker.quota_inspector.inspect_model_quotas.return_value = {
            "success": True,
            "is_safe_to_prompt": False,
            "is_low_quota": True,
            "safety_reason": "Gemini 5-Hour quota (15%) is below threshold (30%)",
            "current_email": outside_email,
            "gemini": {
                "five_hour_percent": 15,
                "five_hour_refresh": "4 hours",
                "weekly_percent": 80,
                "weekly_refresh": "6 days",
            },
        }

        enforce_called = []
        def fake_enforce_quota(label, known_unsafe_res=None):
            enforce_called.append((label, known_unsafe_res))
            return True

        worker._enforce_quota = fake_enforce_quota

        # Run check
        ok = worker._check_pre_dispatch_quota("TestProject")
        assert ok is True  # enforce_quota returned True (recovered)
        assert len(enforce_called) == 1
        label, unsafe_res = enforce_called[0]
        assert unsafe_res.get("is_outside_account") is True
        assert unsafe_res.get("is_safe_to_prompt") is False
        assert "queue1@gmail.com" in unsafe_res.get("safety_reason")
    finally:
        account_manager.accounts = orig_accounts
        account_manager.current_email = orig_email


def test_account_switcher_allows_safe_outside_account():
    """
    Verifies that AccountSwitchWorker when force_switch is False
    will early exit if the active account (registered or outside) has safe quota.
    """
    from app.account_switcher import AccountSwitchWorker
    from app.account_manager import account_manager
    from cdp.state_manager import AntigravityUIState

    orig_accounts = list(account_manager.accounts)
    orig_email = account_manager.current_email

    try:
        account_manager.accounts = [
            GoogleAccount(email="queue1@gmail.com", enabled=True, quota_status="Ready", is_frozen=False),
        ]
        outside_email = "outside.user@gmail.com"
        account_manager.current_email = outside_email

        switcher = AccountSwitchWorker(force_switch=False)
        mock_client = MagicMock()
        mock_client.connect.return_value = (True, "Connected")
        mock_page = MagicMock()
        mock_client.get_page.return_value = mock_page

        mock_state_mgr = MagicMock()
        mock_state_mgr.detect_state.return_value = AntigravityUIState.FRESH_CHAT_READY

        mock_inspector = MagicMock()
        mock_inspector.inspect_model_quotas.return_value = {
            "success": True,
            "is_safe_to_prompt": True,
            "current_email": outside_email,
            "gemini": {"five_hour_percent": 80, "weekly_percent": 90},
        }

        # Mock BrowserOAuthHandler so we can verify it's never instantiated or called
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.account_switcher.AntigravityCDPClient", lambda: mock_client)
            mp.setattr("cdp.state_manager.AntigravityStateManager", lambda p: mock_state_mgr)
            mp.setattr("app.account_switcher.DOMQuotaInspector", lambda p: mock_inspector)
            mock_oauth = MagicMock()
            mp.setattr("app.account_switcher.BrowserOAuthHandler", mock_oauth)

            finished_signals = []
            switcher.sig_finished.connect(lambda s, e, d: finished_signals.append((s, e, d)))

            switcher.run()

            # Must have finished successfully without calling OAuth switch!
            assert len(finished_signals) == 1
            success, email, err = finished_signals[0]
            assert success is True
            assert mock_oauth.call_count == 0
            mock_page.keyboard.press.assert_called_with("Escape")
    finally:
        account_manager.accounts = orig_accounts
        account_manager.current_email = orig_email
