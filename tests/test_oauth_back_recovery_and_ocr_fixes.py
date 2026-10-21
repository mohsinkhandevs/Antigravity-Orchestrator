import json
import os
from unittest.mock import MagicMock, patch
import pytest

from cdp.state_manager import AntigravityStateManager, AntigravityUIState
from cdp.browser_oauth import BrowserOAuthHandler
from app.account_manager import GoogleAccount
from app.browser_ocr_automation import (
    find_exact_email,
    detect_confirmation_page,
    detect_browser_error_page,
    phase2_find_and_click,
    run_browser_account_selection,
    AccountNotInBrowserError,
    BrowserErrorPageException,
)
from app.models import PromptItem, TaskStatus, strip_prompt_command, ensure_prompt_prefix
from cdp.reconciliation import reconcile_prompt_status_from_conversation
from cdp.transcript_inspector import read_last_transcript_steps, inspect_transcript_termination


def test_state_manager_detects_and_resets_oauth_pending():
    mock_page = MagicMock()
    # 1st call: is_oauth_pending -> True
    # 2nd call: reset_oauth_pending_via_back click evaluate -> True
    # 3rd call: post-click check is_oauth_pending -> False
    mock_page.evaluate.side_effect = [True, True, False]
    sm = AntigravityStateManager(mock_page)

    assert sm.is_oauth_pending() is True
    assert sm.reset_oauth_pending_via_back() is True


def test_oauth_retries_same_account_via_back_when_browser_fails_to_return_control():
    mock_page = MagicMock()
    automator = BrowserOAuthHandler(mock_page)
    target_acc = GoogleAccount(email="target@gmail.com")

    with patch("cdp.browser_oauth.AntigravityStateManager.detect_state", return_value=AntigravityUIState.LOGGED_OUT), \
         patch("cdp.browser_oauth.account_manager.accounts", [target_acc]), \
         patch("cdp.browser_oauth.account_manager.set_active_account"), \
         patch.object(automator, "_click_continue_with_google", return_value=True) as mock_cont, \
         patch.object(automator, "_click_back_if_oauth_pending", return_value=True) as mock_back, \
         patch.object(automator, "_wait_and_click_finish", side_effect=[False, True]) as mock_finish, \
         patch.object(automator, "_close_settings_and_focus", return_value=None), \
         patch("cdp.browser_oauth.run_browser_account_selection", return_value={
             "success": True,
             "is_2fa_challenge": False,
             "account_not_in_browser": False,
             "is_error_page": False,
             "error": "",
         }):
        res = automator.execute_account_switch(
            target_account_email="target@gmail.com",
        )

    assert res["success"] is True
    assert res["switched_to_email"] == "target@gmail.com"
    # Should have clicked Continue with Google twice (initial + 1 retry after Back reset)
    assert mock_cont.call_count == 2
    assert mock_finish.call_count == 2
    assert mock_back.call_count >= 2


def test_oauth_rotates_to_next_account_when_account_not_in_browser():
    mock_page = MagicMock()
    automator = BrowserOAuthHandler(mock_page)

    missing_acc = GoogleAccount(email="missing@gmail.com")
    next_acc_mock = GoogleAccount(email="fallback@gmail.com")

    ocr_side_effect = [
        {
            "success": False,
            "is_2fa_challenge": False,
            "account_not_in_browser": True,
            "is_error_page": False,
            "error": "Could not locate 'missing@gmail.com'",
        },
        {
            "success": True,
            "is_2fa_challenge": False,
            "account_not_in_browser": False,
            "is_error_page": False,
            "error": "",
        },
    ]

    with patch("cdp.browser_oauth.AntigravityStateManager.detect_state", return_value=AntigravityUIState.LOGGED_OUT), \
         patch("cdp.browser_oauth.account_manager.accounts", [missing_acc, next_acc_mock]), \
         patch.object(automator, "_click_continue_with_google", return_value=True), \
         patch.object(automator, "_click_back_if_oauth_pending", return_value=True) as mock_back, \
         patch.object(automator, "_wait_and_click_finish", return_value=True), \
         patch.object(automator, "_close_settings_and_focus", return_value=None), \
         patch("cdp.browser_oauth.account_manager.get_next_account", return_value=next_acc_mock), \
         patch("cdp.browser_oauth.account_manager.set_active_account"), \
         patch("cdp.browser_oauth.run_browser_account_selection", side_effect=ocr_side_effect):
        res = automator.execute_account_switch(
            target_account_email="missing@gmail.com",
        )

    assert res["success"] is True
    assert res["switched_to_email"] == "fallback@gmail.com"
    assert mock_back.call_count >= 1


def test_find_exact_email_rejects_telemetry_and_disambiguates_similar_usernames():
    ocr_results = [
        # Self-OCR poisoning line from Antigravity CDP Studio status bar / log console
        ("Status: Switching to testuser786@gmail.com in 294s (Click Change Now)", 450, 920, None, 0.99),
        ("[INFO] Target account 'testuser786@gmail.com'", 400, 880, None, 0.99),
        # Similar prefix account in the browser list
        ("testuser785@gmail.com", 500, 350, None, 0.98),
        ("testuser.div@gmail.com", 500, 410, None, 0.98),
    ]

    # Should NOT match telemetry lines or testuser785 when searching for testuser786
    assert find_exact_email(ocr_results, "testuser786@gmail.com") is None
    # Should NOT match testuser.div when searching for testuser.devs
    assert find_exact_email(ocr_results, "testuser.devs@gmail.com") is None

    # When the exact account is present in browser content, it matches accurately
    ocr_with_target = ocr_results + [("testuser786@gmail.com", 500, 470, None, 0.99)]
    match = find_exact_email(ocr_with_target, "testuser786@gmail.com")
    assert match is not None
    assert match[1] == 500 and match[2] == 470


def test_phase2_skips_directly_to_phase3_when_already_on_confirmation_page():
    confirm_ocr = [
        ("Sign in to Antigravity", 500, 220, None, 0.99),
        ("confirm.user@gmail.com", 500, 270, None, 0.99),
        ("Make sure that you downloaded this app from Google", 500, 340, None, 0.99),
        ("Cancel", 440, 520, None, 0.99),
        ("Sign in", 580, 520, None, 0.99),
    ]
    assert detect_confirmation_page(confirm_ocr) is True

    with patch("app.browser_ocr_automation.try_focus_browser", return_value=True), \
         patch("app.browser_ocr_automation.get_browser_window_info", return_value=None), \
         patch("app.browser_ocr_automation.capture_and_ocr", return_value=(confirm_ocr, None)), \
         patch("app.browser_ocr_automation.safe_click") as mock_click:
        ok = phase2_find_and_click("confirm.user@gmail.com", save_snapshots=False)

    assert ok is True
    # Should not click the email pill when already on the confirmation page for that same email
    mock_click.assert_not_called()


def test_detect_browser_error_page_and_scroll_exhaustion():
    error_ocr = [
        ("500. That's an error.", 400, 250, None, 0.99),
        ("There was an error. Please try again later. That's all we know.", 400, 300, None, 0.98),
    ]
    assert detect_browser_error_page(error_ocr) is not None

    # Test scroll exhaustion raising AccountNotInBrowserError early
    static_list_ocr = [
        ("Choose an account", 500, 200, None, 0.99),
        ("other1@gmail.com", 500, 300, None, 0.99),
        ("other2@gmail.com", 500, 360, None, 0.99),
        ("Use another account", 500, 440, None, 0.99),
    ]
    with patch("app.browser_ocr_automation.try_focus_browser", return_value=True), \
         patch("app.browser_ocr_automation.get_browser_window_info", return_value=None), \
         patch("app.browser_ocr_automation.capture_and_ocr", return_value=(static_list_ocr, None)), \
         patch("app.browser_ocr_automation.scroll_in_page") as mock_scroll:
        with pytest.raises(AccountNotInBrowserError):
            phase2_find_and_click("not_in_list@gmail.com", max_scrolls=10, settle_delay=0.0, save_snapshots=False)

    # Because both 'Use another account' and 'Choose an account' are visible and Y coords don't shift,
    # it should break out after 1 down-scroll and 1 up-scroll instead of 30 total scrolls!
    assert mock_scroll.call_count == 2


def test_reconciliation_does_not_mark_interrupted_as_completed():
    prompt = PromptItem(text="Test prompt", status=TaskStatus.RUNNING, conversation_id="conv-123")
    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"found": True, "state": "INTERRUPTED"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"found": True, "state": "INTERRUPTED"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "is_idle": True, "killed": False, "not_fully_idle": 0}):
        changed = reconcile_prompt_status_from_conversation(prompt)

    assert changed is False
    assert prompt.status == TaskStatus.RUNNING


def test_transcript_inspector_large_line_and_generic_tool_quota_filter(tmp_path):
    conv_id = "test-large-conv"
    log_dir = tmp_path / conv_id / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    transcript_file = log_dir / "transcript.jsonl"

    large_content = "x" * 50000 + " capacity check in file output"
    steps_data = [
        {"step_index": 1, "type": "USER_INPUT", "status": "DONE", "content": "Run check"},
        {"step_index": 2, "type": "GENERIC", "status": "ERROR", "content": large_content, "error": "Command exited with code 1"},
    ]
    with open(transcript_file, "w", encoding="utf-8") as f:
        for s in steps_data:
            f.write(json.dumps(s) + "\n")

    read_steps = read_last_transcript_steps(conv_id, max_steps=2, brain_dir=str(tmp_path), tail_bytes=32768)
    assert len(read_steps) == 2

    term = inspect_transcript_termination(conv_id, brain_dir=str(tmp_path))
    # Should be ERRORED (tool failure), NOT QUOTA_EXHAUSTED despite 'capacity' appearing in tool output
    assert term["state"] == "ERRORED"


def test_strip_prompt_command_preserves_markdown_lists_and_paths():
    md_prompt = "/plan\n- First bullet item\n- Second bullet item"
    cmd, body = strip_prompt_command(md_prompt)
    assert cmd == "/plan"
    assert body.startswith("- First bullet item")

    hr_prompt = "/goal\n---\nSection content"
    cmd2, body2 = strip_prompt_command(hr_prompt)
    assert cmd2 == "/goal"
    assert body2.startswith("---")

    path_prompt = "/usr/local/bin/python script.py"
    cmd3, body3 = strip_prompt_command(path_prompt)
    assert cmd3 == ""
    assert body3 == path_prompt
