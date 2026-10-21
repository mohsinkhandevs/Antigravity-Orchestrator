import pytest
from unittest.mock import MagicMock, patch
from cdp.quota_inspector import DOMQuotaInspector, parse_reset_time_seconds
from cdp.dom_controller import AntigravityDOMController
from cdp.browser_oauth import BrowserOAuthHandler
from app.account_manager import account_manager


def test_parse_reset_time_seconds():
    assert parse_reset_time_seconds("2 hours, 59 minutes") == 2 * 3600 + 59 * 60
    assert parse_reset_time_seconds("6 days, 9 hours") == 6 * 86400 + 9 * 3600
    assert parse_reset_time_seconds("45 minutes") == 45 * 60
    assert parse_reset_time_seconds("less than a minute") == 60
    assert parse_reset_time_seconds("a few seconds") == 60
    assert parse_reset_time_seconds("0 minutes") == 30
    assert parse_reset_time_seconds("unknown") is None
    assert parse_reset_time_seconds("") is None


def test_dom_quota_inspector_extract():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = """
    Models & Usage
    Gemini Models
    Five-Hour Limit Remaining: 65% Resets in 2 hours, 15 minutes
    Weekly Limit Remaining: 80% Resets in 5 days, 12 hours
    Claude & GPT Models
    Five-Hour Limit Remaining: 15%
    """

    inspector = DOMQuotaInspector(mock_page)
    res = inspector._extract_gemini_limits_from_dom()

    assert res["success"] is True
    assert res["five_hour_percent"] == 65
    assert "2 hours" in res["five_hour_refresh"]
    assert res["weekly_percent"] == 80
    assert "5 days" in res["weekly_refresh"]


def test_dom_quota_inspector_extract_multiline_live_format():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = """
    Models & Usage
    Manage your model quota and credits.
    Gemini Models
    Weekly Limit Remaining
    You have used some of your weekly limit, it will fully refresh in 14 hours, 52 minutes.
    49%
    Five Hour Limit Remaining
    You have used some of your 5-hour limit, it will fully refresh in 2 hours, 19 minutes.
    1%
    Claude and GPT models
    Weekly Limit Remaining
    100%
    Five Hour Limit Remaining
    100%
    """

    inspector = DOMQuotaInspector(mock_page)
    res = inspector._extract_gemini_limits_from_dom()

    assert res["success"] is True
    assert res["five_hour_percent"] == 1
    assert res["five_hour_refresh"] == "2 hours, 19 minutes"
    assert res["weekly_percent"] == 49
    assert res["weekly_refresh"] == "14 hours, 52 minutes"


def test_dom_quota_inspector_unknown_is_not_safe(monkeypatch):
    mock_page = MagicMock()
    inspector = DOMQuotaInspector(mock_page)
    monkeypatch.setattr(
        inspector,
        "_extract_gemini_limits_from_dom",
        lambda: {
            "success": False,
            "five_hour_percent": None,
            "five_hour_refresh": "Unknown",
            "weekly_percent": None,
            "weekly_refresh": "Unknown",
        },
    )
    res = inspector.inspect_model_quotas(close_settings=False)
    assert res["is_safe_to_prompt"] is False
    assert "Unable to read" in res["safety_reason"]



def test_dom_controller_is_generating():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = True

    controller = AntigravityDOMController(mock_page)
    assert controller.is_generating() is True

    # When evaluate returns False (no active cancel button in active view)
    mock_page.evaluate.return_value = False
    assert controller.is_generating() is False


def test_dom_controller_inject_multiline_plan(monkeypatch):
    mock_page = MagicMock()
    mock_input = MagicMock()
    mock_input.is_visible.return_value = True
    mock_page.locator.return_value.first = mock_input

    copied_texts = []
    monkeypatch.setattr("pyperclip.copy", lambda text: copied_texts.append(text))
    monkeypatch.setattr("pyperclip.paste", lambda: "original_clipboard_data")

    controller = AntigravityDOMController(mock_page)
    prompt = "/plan # ROLE: Senior Dev\nLine 1\nLine 2"
    res = controller.inject_and_submit_prompt(prompt, post_submit_delay=0.0)

    assert res is True
    # Verify slash command was typed
    mock_input.type.assert_called_once_with("/plan", delay=35)
    # Verify multiline body was copied to clipboard and then original clipboard was restored
    assert copied_texts[0] == "# ROLE: Senior Dev\nLine 1\nLine 2"
    assert copied_texts[-1] == "original_clipboard_data"
    # Verify Control+V was pressed to paste
    mock_page.keyboard.press.assert_any_call("Control+V")
    # Verify Enter was pressed to submit
    mock_page.keyboard.press.assert_any_call("Enter")


def test_dom_controller_inject_standard_prompt(monkeypatch):
    mock_page = MagicMock()
    mock_input = MagicMock()
    mock_input.is_visible.return_value = True
    mock_page.locator.return_value.first = mock_input

    copied_texts = []
    monkeypatch.setattr("pyperclip.copy", lambda text: copied_texts.append(text))
    monkeypatch.setattr("pyperclip.paste", lambda: "original_clipboard_data")

    controller = AntigravityDOMController(mock_page)
    prompt = "Simple multi-line:\nStep 1\nStep 2"
    res = controller.inject_and_submit_prompt(prompt, post_submit_delay=0.0)

    assert res is True
    assert mock_input.type.call_count == 0
    assert copied_texts[0] == "Simple multi-line:\nStep 1\nStep 2"
    assert copied_texts[-1] == "original_clipboard_data"
    mock_page.keyboard.press.assert_any_call("Control+V")
    mock_page.keyboard.press.assert_any_call("Enter")


def test_dom_controller_is_generating_active_view():
    mock_page = MagicMock()

    # Case 1: Active cancel button in main view outside sidebar
    mock_page.evaluate.return_value = True
    controller = AntigravityDOMController(mock_page)
    assert controller.is_generating() is True

    # Case 2: No active cancel button in main view (e.g. only sidebar tasks running)
    mock_page.evaluate.return_value = False
    assert controller.is_generating() is False


def test_is_fresh_empty_chat_open():
    mock_page = MagicMock()
    mock_input = MagicMock()
    mock_input.is_visible.return_value = True
    mock_page.locator.return_value.first = mock_input

    controller = AntigravityDOMController(mock_page)

    # 1. When generating in active view, should return False
    controller.is_generating = MagicMock(return_value=True)
    assert controller.is_fresh_empty_chat_open() is False

    # 2. When active view has turns in DOM, should return False
    controller.is_generating = MagicMock(return_value=False)
    mock_page.evaluate.return_value = True  # has_turns_or_history = True
    assert controller.is_fresh_empty_chat_open() is False

    # 3. When URL has /c/<id>, should return False
    mock_page.evaluate.return_value = False  # has_turns_or_history = False
    mock_page.url = "https://127.0.0.1:62325/c/abc-123"
    assert controller.is_fresh_empty_chat_open() is False

    # 4. Clean empty chat on root URL with 0 turns and visible input
    mock_page.url = "https://127.0.0.1:62325/"
    mock_page.evaluate.return_value = False
    assert controller.is_fresh_empty_chat_open() is True


def test_open_new_conversation_waits_when_busy():
    mock_page = MagicMock()
    controller = AntigravityDOMController(mock_page)

    # Agent is generating
    controller.is_generating = MagicMock(return_value=True)
    controller.wait_for_completion = MagicMock(return_value=True)
    controller.is_fresh_empty_chat_open = MagicMock(return_value=True)

    res = controller.open_new_conversation(force=False, wait_if_busy=True)
    assert res is True
    # Verify wait_for_completion was called before proceeding
    controller.wait_for_completion.assert_called_once()


def test_select_target_project_already_selected():
    mock_page = MagicMock()
    mock_trigger = MagicMock()
    mock_trigger.is_visible.return_value = True
    mock_trigger.text_content.return_value = "Agrico-V2"
    mock_trigger.get_attribute.return_value = "Select project, current: Agrico-V2"

    controller = AntigravityDOMController(mock_page)
    controller._get_project_trigger_locator = MagicMock(return_value=mock_trigger)

    assert controller.select_target_project("Agrico-V2") is True
    mock_trigger.click.assert_not_called()


def test_select_target_project_switch():
    mock_page = MagicMock()
    mock_trigger = MagicMock()
    mock_trigger.is_visible.return_value = True
    mock_trigger.text_content.return_value = "Antigravity Automation Win UIA"
    mock_trigger.get_attribute.return_value = "Select project, current: Antigravity Automation Win UIA"

    mock_item = MagicMock()
    mock_item.is_visible.return_value = True
    mock_item.element_handle.return_value = MagicMock()

    mock_candidates = MagicMock()
    mock_candidates.count.return_value = 1
    mock_candidates.nth.return_value = mock_item

    mock_page.locator.return_value = mock_candidates
    mock_page.evaluate.return_value = False  # Not in sidebar

    controller = AntigravityDOMController(mock_page)
    controller._get_project_trigger_locator = MagicMock(return_value=mock_trigger)

    assert controller.select_target_project("Agrico-V2") is True
    mock_trigger.click.assert_called_once()
    mock_item.click.assert_called_once()


def test_select_target_project_no_project_already_selected():
    mock_page = MagicMock()
    mock_trigger = MagicMock()
    mock_trigger.is_visible.return_value = True
    mock_trigger.text_content.return_value = "No Project"
    mock_trigger.get_attribute.return_value = "Select project, current: No Project"

    controller = AntigravityDOMController(mock_page)
    controller._get_project_trigger_locator = MagicMock(return_value=mock_trigger)

    assert controller.select_target_project("No Project") is True
    assert controller.select_target_project("") is True
    mock_trigger.click.assert_not_called()


def test_select_target_project_switch_to_no_project():
    mock_page = MagicMock()
    mock_trigger = MagicMock()
    mock_trigger.is_visible.return_value = True
    mock_trigger.text_content.return_value = "Agrico-V2"
    mock_trigger.get_attribute.return_value = "Select project, current: Agrico-V2"

    mock_item = MagicMock()
    mock_item.is_visible.return_value = True
    mock_item.element_handle.return_value = MagicMock()

    mock_candidates = MagicMock()
    mock_candidates.count.return_value = 1
    mock_candidates.nth.return_value = mock_item

    mock_page.locator.return_value = mock_candidates
    mock_page.evaluate.return_value = False  # Not in sidebar

    controller = AntigravityDOMController(mock_page)
    controller._get_project_trigger_locator = MagicMock(return_value=mock_trigger)

    assert controller.select_target_project("") is True
    mock_trigger.click.assert_called_once()
    mock_item.click.assert_called_once()


def test_browser_oauth_read_current_email():
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        True,  # has_modal = True
        {
            "navText": "Test User\ndev.test@example.com",
            "modalText": "Account\nManage your plan, credentials, and general preferences.\nEmail\ndev.test@example.com\nSign Out",
        },
    ]

    handler = BrowserOAuthHandler(mock_page)
    email = handler._read_current_email()
    assert email == "dev.test@example.com"


def test_browser_oauth_open_profile_and_click_sign_out():
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    # _open_profile_page: is_dialog_open=False -> press Control+, -> is_dialog_open=True -> clicked_account=True -> is_account_view=True
    mock_page.evaluate.side_effect = [
        False,
        True,
        True,
        True,
    ]
    handler = BrowserOAuthHandler(mock_page)
    assert handler._open_profile_page() is True
    mock_page.keyboard.press.assert_called_once_with("Control+,")

    # _click_sign_out: has_sign_out=True -> clicked=True -> confirm dialog check=False
    mock_page.evaluate.side_effect = [
        True,
        True,
        False,
    ]
    assert handler._click_sign_out() is True


def test_browser_oauth_handle_error_screen_continue_with_different_account():
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    # evaluate returns True (clicked 'Continue with different account')
    mock_page.evaluate.return_value = True

    handler = BrowserOAuthHandler(mock_page)
    assert handler._handle_error_screen_continue_with_different_account() is True


def test_browser_oauth_wait_and_click_finish_recovers_from_error_screen(monkeypatch):
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    handler = BrowserOAuthHandler(mock_page)

    # First call to evaluate: state is None (screen is error screen)
    # Second call to evaluate: state is 'clicked_finish'
    mock_page.evaluate.side_effect = [
        None,
        'clicked_finish',
    ]

    # Mock error screen detector to return True on 1st check, then False
    error_checks = [True, False]
    monkeypatch.setattr(
        handler,
        "_handle_error_screen_continue_with_different_account",
        lambda log=None: error_checks.pop(0) if error_checks else False,
    )
    # Mock re-clicking continue with google
    mock_click_google = MagicMock(return_value=True)
    monkeypatch.setattr(handler, "_click_continue_with_google", mock_click_google)

    assert handler._wait_and_click_finish(timeout=2.0) is True
    mock_click_google.assert_called_once()


def test_browser_oauth_execute_account_switch_integrated(monkeypatch):
    from cdp.browser_oauth import BrowserOAuthHandler
    from app.account_manager import account_manager, GoogleAccount

    original_accounts = list(account_manager.accounts)
    original_current = account_manager.current_email
    monkeypatch.setattr(account_manager, "save_accounts", lambda: None)

    account_manager.accounts = [
        GoogleAccount(email="target@gmail.com", display_name="Target"),
    ]

    mock_page = MagicMock()
    handler = BrowserOAuthHandler(mock_page)

    monkeypatch.setattr(handler, "_open_profile_page", lambda log=None: True)
    monkeypatch.setattr(handler, "_read_current_email", lambda log=None: "prev@gmail.com")
    monkeypatch.setattr(handler, "_click_sign_out", lambda log=None: True)
    monkeypatch.setattr(handler, "_click_continue_with_google", lambda log=None: True)

    mock_ocr = MagicMock(return_value={"success": True, "step": "completed", "error": ""})
    monkeypatch.setattr("cdp.browser_oauth.run_browser_account_selection", mock_ocr)

    monkeypatch.setattr(handler, "_wait_and_click_finish", lambda *args, **kwargs: True)
    monkeypatch.setattr(handler, "_close_settings_and_focus", lambda *args, **kwargs: True)

    steps_notified = []
    res = handler.execute_account_switch(
        target_account_email="target@gmail.com",
        step_callback=lambda s, ok, msg: steps_notified.append(s),
    )

    assert res["success"] is True
    assert res["switched_to_email"] == "target@gmail.com"
    mock_ocr.assert_called_once_with(
        target_email="target@gmail.com",
        log_callback=res.get("log_callback", mock_ocr.call_args[1].get("log_callback")),
        stop_event=None,
        early_completion_checker=handler._is_antigravity_authenticated_or_ready,
    )
    assert 7 in steps_notified  # Step 7 Browser OCR
    assert 8 in steps_notified  # Step 8 Finish
    assert 9 in steps_notified  # Step 9 Close Settings

    account_manager.accounts = original_accounts
    account_manager.current_email = original_current


def test_is_conversation_generating_resilient_to_viewing_old_chat():
    """
    Scenario: User starts prompt in conv_target. While conv_target is generating,
    the user navigates to conv_old to read previous output.
    conv_old has no stop button in the active viewport.
    is_conversation_generating MUST still return True because conv_target has
    an active spinner in the sidebar.
    """
    mock_page = MagicMock()
    controller = AntigravityDOMController(mock_page)

    with patch("cdp.conversation_db_inspector.get_conversation_summary", return_value={"found": False}), \
         patch("cdp.conversation_db_inspector.find_running_conversations_in_db", return_value=[]):
        # Active viewport shows old chat
        controller.get_active_conversation_id = MagicMock(return_value="conv_old")
        # Active viewport has NO cancel button (since old chat is finished)
        controller.is_generating = MagicMock(return_value=False)

        # 1. Target conversation still shows spinner in sidebar
        controller.is_sidebar_conversation_generating = MagicMock(return_value=True)
        assert controller.is_conversation_generating(
            conversation_id="conv_target",
            project_name="Agrico-V2",
        ) is True

        # 2. Target conversation sidebar row not found, but project header has running chat
        controller.is_sidebar_conversation_generating = MagicMock(return_value=None)
        controller.find_running_conversation_for_project = MagicMock(return_value={
            "id": "conv_target",
            "title": "Running Agent",
            "project": "Agrico-V2",
            "is_running": True,
        })
        assert controller.is_conversation_generating(
            conversation_id="conv_target",
            project_name="Agrico-V2",
        ) is True

        # 3. Agent finished completely (sidebar spinner gone and no project chat running)
        controller.is_sidebar_conversation_generating = MagicMock(return_value=False)
        controller.find_running_conversation_for_project = MagicMock(return_value=None)
        assert controller.is_conversation_generating(
            conversation_id="conv_target",
            project_name="Agrico-V2",
        ) is False


def test_handle_error_screen_continue_with_different_account():
    mock_page = MagicMock()
    handler = BrowserOAuthHandler(mock_page)

    # 1. When evaluate returns True (clicked via leaf element)
    mock_page.evaluate.return_value = True
    assert handler._handle_error_screen_continue_with_different_account() is True

    # 2. When evaluate returns False but Playwright text locator is visible
    mock_page.evaluate.return_value = False
    mock_locator = MagicMock()
    mock_locator.is_visible.return_value = True
    mock_page.locator.return_value.first = mock_locator
    assert handler._handle_error_screen_continue_with_different_account() is True
    assert mock_locator.click.called

    # 3. When nothing matches
    mock_locator.is_visible.return_value = False
    assert handler._handle_error_screen_continue_with_different_account() is False


def test_wait_and_click_finish_retries_same_account_3_times_then_fails_over():
    from app.account_manager import GoogleAccount
    original_accounts = list(account_manager.accounts)
    account_manager.accounts = [
        GoogleAccount(email="bad@gmail.com", enabled=True),
        GoogleAccount(email="good@gmail.com", enabled=True),
    ]

    mock_page = MagicMock()
    handler = BrowserOAuthHandler(mock_page)

    # 3 error screens for bad@gmail.com, then finish button clicked for good@gmail.com
    calls = {"eval": 0}
    def mock_eval(js):
        calls["eval"] += 1
        if calls["eval"] <= 3:
            return None
        return "clicked_finish"

    mock_page.evaluate.side_effect = mock_eval

    ocr_calls = []
    def fake_ocr(target_email, **kwargs):
        ocr_calls.append(target_email)
        return {"success": True}

    final_email_holder = []
    with patch.object(handler, "_handle_error_screen_continue_with_different_account", side_effect=[True, True, True, False]), \
         patch.object(handler, "_click_continue_with_google", return_value=True), \
         patch("cdp.browser_oauth.run_browser_account_selection", side_effect=fake_ocr):

        res = handler._wait_and_click_finish(
            timeout=2.0,
            target_email="bad@gmail.com",
            final_email_container=final_email_holder,
        )
        assert res is True
        # Attempts 1 and 2 retried bad@gmail.com, attempt 3 failed over to good@gmail.com
        assert ocr_calls == ["bad@gmail.com", "bad@gmail.com", "good@gmail.com"]
        assert final_email_holder == ["good@gmail.com"]

    account_manager.accounts = original_accounts


def test_browser_oauth_wait_and_click_get_started():
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    mock_page.evaluate.return_value = "clicked_finish"

    handler = BrowserOAuthHandler(mock_page)
    final_emails = []
    res = handler._wait_and_click_finish(
        timeout=2.0,
        target_email="test@gmail.com",
        final_email_container=final_emails,
    )
    assert res is True
    assert final_emails == ["test@gmail.com"]


def test_browser_oauth_wait_and_click_get_started_and_dismisses_terms():
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    # evaluate returns clicked_finish (Get started / Finish)
    mock_page.evaluate.return_value = "clicked_finish"

    logged_messages = []
    handler = BrowserOAuthHandler(mock_page)
    # Mock _dismiss_post_onboarding_terms_if_present to simulate dismissing terms notice
    handler._dismiss_post_onboarding_terms_if_present = MagicMock(return_value=True)

    final_emails = []
    res = handler._wait_and_click_finish(
        log=lambda lvl, msg: logged_messages.append((lvl, msg)),
        timeout=2.0,
        target_email="test@gmail.com",
        final_email_container=final_emails,
    )
    assert res is True
    assert final_emails == ["test@gmail.com"]
    assert any("Get started" in msg for lvl, msg in logged_messages)
    handler._dismiss_post_onboarding_terms_if_present.assert_called()


def test_browser_oauth_wait_and_click_workspace_ready():
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    mock_page.evaluate.return_value = "workspace_ready"

    handler = BrowserOAuthHandler(mock_page)
    final_emails = []
    res = handler._wait_and_click_finish(
        timeout=2.0,
        target_email="direct@gmail.com",
        final_email_container=final_emails,
    )
    assert res is True
    assert final_emails == ["direct@gmail.com"]


def test_browser_oauth_wait_and_click_accept_and_continue():
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    mock_page.evaluate.return_value = "clicked_accept"

    handler = BrowserOAuthHandler(mock_page)
    final_emails = []
    res = handler._wait_and_click_finish(
        timeout=2.0,
        target_email="accepted@gmail.com",
        final_email_container=final_emails,
    )
    assert res is True
    assert final_emails == ["accepted@gmail.com"]


def test_browser_oauth_dismiss_post_onboarding_terms_if_present():
    from cdp.browser_oauth import BrowserOAuthHandler

    mock_page = MagicMock()
    mock_page.evaluate.return_value = True

    handler = BrowserOAuthHandler(mock_page)
    assert handler._dismiss_post_onboarding_terms_if_present() is True


def test_dom_controller_dismiss_security_notice_if_present():
    from cdp.dom_controller import AntigravityDOMController

    mock_page = MagicMock()
    mock_page.evaluate.return_value = True

    controller = AntigravityDOMController(mock_page)
    assert controller.dismiss_security_notice_if_present() is True


def test_inject_prompt_preserves_and_restores_user_clipboard(monkeypatch):
    mock_page = MagicMock()
    mock_input = MagicMock()
    mock_input.is_visible.return_value = True
    mock_page.locator.return_value.first = mock_input

    clipboard_state = {"content": "SECRET_USER_API_KEY_12345"}

    def fake_copy(val):
        clipboard_state["content"] = val

    def fake_paste():
        return clipboard_state["content"]

    monkeypatch.setattr("pyperclip.copy", fake_copy)
    monkeypatch.setattr("pyperclip.paste", fake_paste)

    controller = AntigravityDOMController(mock_page)
    res = controller.inject_and_submit_prompt("Test prompt", post_submit_delay=0.0, restore_clipboard=True)

    assert res is True
    # Clipboard should be restored to the user's secret content
    assert clipboard_state["content"] == "SECRET_USER_API_KEY_12345"


def test_inject_prompt_skip_restore_when_disabled(monkeypatch):
    mock_page = MagicMock()
    mock_input = MagicMock()
    mock_input.is_visible.return_value = True
    mock_page.locator.return_value.first = mock_input

    clipboard_state = {"content": "OLD_CLIPBOARD"}

    def fake_copy(val):
        clipboard_state["content"] = val

    def fake_paste():
        return clipboard_state["content"]

    monkeypatch.setattr("pyperclip.copy", fake_copy)
    monkeypatch.setattr("pyperclip.paste", fake_paste)

    controller = AntigravityDOMController(mock_page)
    res = controller.inject_and_submit_prompt("New prompt body", post_submit_delay=0.0, restore_clipboard=False)

    assert res is True
    # Clipboard should contain the prompt since restoration was disabled
    assert clipboard_state["content"] == "New prompt body"


def test_account_manager_clean_default_fallback_seed(tmp_path):
    from app.account_manager import AccountManager
    non_existent = tmp_path / "clean_empty_accounts.json"
    mgr = AccountManager(filepath=str(non_existent))

    # Priority 3 fallback seed must be clean and empty without any personal emails
    assert mgr.accounts == []
    assert mgr.current_email == ""


