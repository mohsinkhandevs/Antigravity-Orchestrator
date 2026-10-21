import pytest
from unittest.mock import MagicMock, patch
from cdp.state_manager import AntigravityStateManager, AntigravityUIState
from cdp.dom_controller import AntigravityDOMController
from cdp.browser_oauth import BrowserOAuthHandler
from cdp.quota_inspector import DOMQuotaInspector
from app.account_manager import account_manager, GoogleAccount
from app.models import PromptItem, TaskStatus


@pytest.fixture(autouse=True)
def setup_test_accounts():
    account_manager.accounts = [
        GoogleAccount(email="user1@gmail.com", quota_status="Active"),
        GoogleAccount(email="user2@gmail.com", quota_status="Eligible"),
    ]
    account_manager.current_email = "user1@gmail.com"


# ── State Detection Tests ──────────────────────────────────────────────────

def test_detect_state_logged_out():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "LOGGED_OUT"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.LOGGED_OUT


def test_detect_state_auth_error_screen():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "AUTH_ERROR_SCREEN"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.AUTH_ERROR_SCREEN


def test_detect_state_terms_modal_active():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "TERMS_MODAL_ACTIVE"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.TERMS_MODAL_ACTIVE


def test_detect_state_finish_auth_button():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "FINISH_AUTH_BUTTON"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.FINISH_AUTH_BUTTON


def test_detect_state_settings_modal_open():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "SETTINGS_MODAL_OPEN"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.SETTINGS_MODAL_OPEN


def test_detect_state_generating_in_progress():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "GENERATING_IN_PROGRESS"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.GENERATING_IN_PROGRESS


def test_detect_state_chat_quota_exhausted():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "CHAT_QUOTA_EXHAUSTED"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.CHAT_QUOTA_EXHAUSTED


def test_detect_state_fresh_chat_ready():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "FRESH_CHAT_READY"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.FRESH_CHAT_READY


def test_detect_state_idle_existing_chat():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "IDLE_EXISTING_CHAT"
    sm = AntigravityStateManager(mock_page)

    assert sm.detect_state() == AntigravityUIState.IDLE_EXISTING_CHAT


# ── Normalization Tests ────────────────────────────────────────────────────

def test_normalize_to_workspace_when_already_ready():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = "FRESH_CHAT_READY"
    sm = AntigravityStateManager(mock_page)

    assert sm.normalize_to_workspace() is True


def test_normalize_to_workspace_recovers_from_settings_open():
    mock_page = MagicMock()
    # First returns SETTINGS_MODAL_OPEN, then FRESH_CHAT_READY
    mock_page.evaluate.side_effect = ["SETTINGS_MODAL_OPEN", "FRESH_CHAT_READY"]
    sm = AntigravityStateManager(mock_page)

    res = sm.normalize_to_workspace()
    assert res is True
    mock_page.keyboard.press.assert_called_with("Escape")


def test_normalize_to_workspace_recovers_from_logged_out():
    mock_page = MagicMock()
    # First returns LOGGED_OUT, then FRESH_CHAT_READY
    mock_page.evaluate.side_effect = ["LOGGED_OUT", "FRESH_CHAT_READY"]
    sm = AntigravityStateManager(mock_page)

    mock_oauth = MagicMock()
    mock_oauth.execute_account_switch.return_value = {"success": True, "switched_to_email": "user2@gmail.com"}

    res = sm.normalize_to_workspace(oauth_handler=mock_oauth)
    assert res is True
    mock_oauth.execute_account_switch.assert_called_once()


def test_normalize_to_workspace_recovers_from_terms_modal():
    mock_page = MagicMock()
    # First returns TERMS_MODAL_ACTIVE, then clicks accept, then returns FRESH_CHAT_READY
    mock_page.evaluate.side_effect = ["TERMS_MODAL_ACTIVE", True, "FRESH_CHAT_READY"]
    sm = AntigravityStateManager(mock_page)

    res = sm.normalize_to_workspace()
    assert res is True


# ── BrowserOAuthHandler Dynamic Resumability Tests ─────────────────────────

@patch("cdp.browser_oauth.run_browser_account_selection")
def test_browser_oauth_bypasses_signout_when_already_logged_out(mock_browser_ocr):
    mock_browser_ocr.return_value = {"success": True}

    mock_page = MagicMock()
    def eval_router(script):
        if "LOGGED_OUT" in script:
            return "LOGGED_OUT"
        if "different account" in script:
            return False
        if "completionBtn" in script or "workspace_ready" in script:
            return "workspace_ready"
        if "settings-modal-container" in script:
            return False
        return True

    mock_page.evaluate.side_effect = eval_router

    handler = BrowserOAuthHandler(mock_page)
    # Ensure it does NOT try to open settings or click sign out
    handler._open_profile_page = MagicMock()
    handler._click_sign_out = MagicMock()

    res = handler.execute_account_switch()

    assert res["success"] is True
    assert res["switched_to_email"] == "user2@gmail.com"
    # Verify sign-out steps were completely bypassed
    handler._open_profile_page.assert_not_called()
    handler._click_sign_out.assert_not_called()
    mock_browser_ocr.assert_called_once()


@patch("cdp.browser_oauth.run_browser_account_selection")
def test_browser_oauth_completes_when_finish_button_already_visible(mock_browser_ocr):
    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        "FINISH_AUTH_BUTTON", # state_mgr.detect_state()
        True,                 # _click_finish JS click
        False,                # _close_settings_and_focus
    ]

    handler = BrowserOAuthHandler(mock_page)
    handler._open_profile_page = MagicMock()

    res = handler.execute_account_switch(target_account_email="user1@gmail.com")

    assert res["success"] is True
    assert res["switched_to_email"] == "user1@gmail.com"
    # Browser OCR should not even need to be called
    mock_browser_ocr.assert_not_called()
    handler._open_profile_page.assert_not_called()


# ── DOMQuotaInspector Logged-Out Guard Tests ───────────────────────────────

def test_quota_inspector_detects_logged_out_and_skips_settings():
    mock_page = MagicMock()
    # State detection returns LOGGED_OUT
    mock_page.evaluate.return_value = "LOGGED_OUT"

    inspector = DOMQuotaInspector(mock_page)
    inspector._ensure_settings_dialog_open = MagicMock()

    res = inspector.inspect_model_quotas()

    assert res["is_logged_out"] is True
    assert res["is_safe_to_prompt"] is False
    assert res["read_failed"] is False
    assert "logged out" in res["safety_reason"].lower()
    # Ensure settings was never attempted to be opened
    inspector._ensure_settings_dialog_open.assert_not_called()


# ── Actionability Overlay Recovery & Terms Modal Tests ─────────────────────

def test_inject_and_submit_prompt_recovers_from_overlay_actionability_timeout():
    mock_page = MagicMock()
    controller = AntigravityDOMController(mock_page)

    mock_input = MagicMock()
    # First click attempt raises TimeoutError (overlay blocking), second attempt succeeds
    mock_input.click.side_effect = [Exception("Element is obscured by <div class='modal'>"), None]
    controller._get_chat_input_locator = MagicMock(return_value=mock_input)
    controller.dismiss_security_notice_if_present = MagicMock(return_value=True)

    with patch("pyperclip.copy"):
        res = controller.inject_and_submit_prompt("Test prompt text")

    assert res is True
    assert mock_input.click.call_count == 2
    # Verify dismiss_security_notice_if_present was called during recovery
    assert controller.dismiss_security_notice_if_present.call_count >= 2


def test_dismiss_post_onboarding_terms_checks_checkbox_and_clicks_button():
    mock_page = MagicMock()
    handler = BrowserOAuthHandler(mock_page)

    mock_cb = MagicMock()
    mock_cb.is_visible.return_value = True
    mock_cb.is_checked.return_value = False

    mock_btn = MagicMock()
    mock_btn.is_visible.return_value = True

    def locator_side_effect(sel):
        if "checkbox" in sel:
            return MagicMock(first=mock_cb)
        if "Accept" in sel:
            return MagicMock(first=mock_btn)
        return MagicMock(first=MagicMock(is_visible=MagicMock(return_value=False)))

    mock_page.locator.side_effect = locator_side_effect

    res = handler._dismiss_post_onboarding_terms_if_present()

    assert res is True
    mock_cb.click.assert_called_once()
    mock_btn.click.assert_called_once()


def test_normalize_to_workspace_bypasses_terms_when_button_missing_but_chat_input_visible():
    mock_page = MagicMock()
    # 1. detect_state returns TERMS_MODAL_ACTIVE
    # 2. check_ready evaluate returns True (chat input is visible)
    mock_page.evaluate.side_effect = ["TERMS_MODAL_ACTIVE", True]
    sm = AntigravityStateManager(mock_page)
    sm._click_accept_terms = MagicMock(return_value=False)

    res = sm.normalize_to_workspace()
    assert res is True
    sm._click_accept_terms.assert_called_once()


