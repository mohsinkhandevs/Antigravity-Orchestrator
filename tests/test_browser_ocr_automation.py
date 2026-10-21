"""
Unit tests for Browser OCR Account Selection Automation module.
"""

import threading
import pytest
from unittest.mock import patch, MagicMock

from app.browser_ocr_automation import (
    find_text_on_screen,
    find_exact_email,
    find_sign_in_button,
    detect_auth_success_screen,
    detect_2fa_challenge,
    find_back_button,
    trigger_back_navigation,
    TwoFactorChallengeError,
    run_browser_account_selection,
    phase3_wait_and_click_sign_in,
    get_screen_virtual_offset,
    safe_click,
    safe_move_to,
    safe_scroll,
    scroll_in_page,
)



def test_find_text_on_screen():
    mock_ocr = [
        ("Choose an account", 500, 200, [[0, 0], [10, 0], [10, 10], [0, 10]], 0.95),
        ("alice@gmail.com", 500, 300, [[0, 0], [10, 0], [10, 10], [0, 10]], 0.98),
        ("bob@yahoo.com", 500, 400, [[0, 0], [10, 0], [10, 10], [0, 10]], 0.92),
    ]

    res = find_text_on_screen(mock_ocr, "@gmail.com")
    assert len(res) == 1
    assert res[0][0] == "alice@gmail.com"

    res_ci = find_text_on_screen(mock_ocr, "CHOOSE")
    assert len(res_ci) == 1
    assert res_ci[0][0] == "Choose an account"

    res_none = find_text_on_screen(mock_ocr, "nonexistent")
    assert len(res_none) == 0


def test_find_exact_email_multi_tier():
    # Tier 1: Exact match
    mock_ocr_tier1 = [
        ("developer.test@example.com", 600, 250, None, 0.99),
        ("other@gmail.com", 600, 350, None, 0.95),
    ]
    match1 = find_exact_email(mock_ocr_tier1, "developer.test@example.com")
    assert match1 is not None
    assert match1[0] == "developer.test@example.com"
    assert match1[1] == 600
    assert match1[2] == 250

    # Tier 2: Substring in line (e.g. "Test User (developer.test@example.com)")
    mock_ocr_tier2 = [
        ("Test User developer.test@example.com", 550, 260, None, 0.91),
    ]
    match2 = find_exact_email(mock_ocr_tier2, "developer.test@example.com")
    assert match2 is not None
    assert match2[1] == 550

    # Tier 3: Username with @ (e.g. OCR missed the domain suffix or cut off)
    mock_ocr_tier3 = [
        ("developer.test@", 520, 270, None, 0.88),
    ]
    match3 = find_exact_email(mock_ocr_tier3, "developer.test@example.com")
    assert match3 is not None
    assert match3[1] == 520

    # Tier 4: Username without @ if long enough (> 8 chars)
    mock_ocr_tier4 = [
        ("developer.test", 510, 280, None, 0.85),
    ]
    match4 = find_exact_email(mock_ocr_tier4, "developer.test@example.com")
    assert match4 is not None
    assert match4[1] == 510

    # Negative: Different account
    no_match = find_exact_email(mock_ocr_tier1, "unrelated_user@gmail.com")
    assert no_match is None


def test_find_sign_in_button():
    # Exact "Sign in"
    mock_ocr = [
        ("Sign in with Google", 500, 100, None, 0.95),  # Should be ignored (header)
        ("Sign in", 650, 450, None, 0.98),               # Should be matched
        ("To continue, first verify it's you", 500, 200, None, 0.90),
    ]
    btn = find_sign_in_button(mock_ocr)
    assert btn is not None
    assert btn[0] == "Sign in"
    assert btn[1] == 650
    assert btn[2] == 450

    # "Signin" variant
    mock_ocr_signin = [
        ("Signin", 700, 400, None, 0.96),
    ]
    btn2 = find_sign_in_button(mock_ocr_signin)
    assert btn2 is not None
    assert btn2[0] == "Signin"

    # Only header "Sign in with Google" -> should NOT match as the button
    mock_ocr_header_only = [
        ("Sign in with Google", 500, 100, None, 0.95),
        ("Use your Google Account", 500, 150, None, 0.92),
    ]
    btn_header = find_sign_in_button(mock_ocr_header_only)
    assert btn_header is None


def test_run_browser_account_selection_cancellation():
    stop_event = threading.Event()
    stop_event.set()  # Pre-trigger stop

    logs = []
    res = run_browser_account_selection(
        target_email="test@gmail.com",
        log_callback=lambda lvl, m: logs.append((lvl, m)),
        stop_event=stop_event,
        save_snapshots=False,
    )
    assert res["success"] is False
    assert "Cancelled by user" in res["error"]


@patch("app.browser_ocr_automation.phase1_wait_for_page")
@patch("app.browser_ocr_automation.phase2_find_and_click")
@patch("app.browser_ocr_automation.phase3_wait_and_click_sign_in")
def test_run_browser_account_selection_success(mock_p3, mock_p2, mock_p1):
    mock_p1.return_value = True
    mock_p2.return_value = True
    mock_p3.return_value = True

    res = run_browser_account_selection(
        target_email="developer.test@example.com",
        save_snapshots=False,
    )

    assert res["success"] is True
    assert res["step"] == "completed"
    assert res["target_email"] == "developer.test@example.com"
    assert res["duration_seconds"] >= 0.0

    mock_p1.assert_called_once()
    mock_p2.assert_called_once()
    mock_p3.assert_called_once()


def test_detect_2fa_challenge():
    # Positive case: 2FA challenge detected
    ocr_challenge = [
        ("2-Step Verification", 500, 200, None, 0.95),
        ("Check your phone", 500, 250, None, 0.90),
        ("Google sent a notification to your phone", 500, 300, None, 0.88),
    ]
    assert detect_2fa_challenge(ocr_challenge) is True

    # Positive case: challenge/dp URL or text
    ocr_dp = [
        ("accounts.google.com/v3/signin/challenge/dp", 400, 50, None, 0.90),
        ("Verify it's you", 500, 200, None, 0.95),
    ]
    assert detect_2fa_challenge(ocr_dp) is True

    # Negative case: Normal sign-in or chooser page
    ocr_normal = [
        ("Choose an account", 500, 200, None, 0.95),
        ("user@gmail.com", 500, 250, None, 0.98),
        ("Sign in", 600, 400, None, 0.99),
    ]
    assert detect_2fa_challenge(ocr_normal) is False


def test_find_back_button():
    ocr_with_back = [
        ("Back", 200, 100, None, 0.95),
        ("2-Step Verification", 500, 200, None, 0.90),
    ]
    btn = find_back_button(ocr_with_back)
    assert btn is not None
    assert btn[0] == "Back"
    assert btn[1] == 200

    ocr_arrow = [
        ("←", 150, 100, None, 0.90),
    ]
    btn_arrow = find_back_button(ocr_arrow)
    assert btn_arrow is not None
    assert btn_arrow[0] == "←"

    ocr_none = [
        ("Sign in", 500, 300, None, 0.95),
    ]
    assert find_back_button(ocr_none) is None


def test_find_sign_in_button_extended_keywords():
    # "Continue" button
    ocr_continue = [
        ("Continue", 600, 450, None, 0.96),
    ]
    btn = find_sign_in_button(ocr_continue)
    assert btn is not None
    assert btn[0] == "Continue"

    # "Allow" button
    ocr_allow = [
        ("Allow", 600, 450, None, 0.96),
    ]
    btn_allow = find_sign_in_button(ocr_allow)
    assert btn_allow is not None
    assert btn_allow[0] == "Allow"

    # "Next" button
    ocr_next = [
        ("Next", 600, 450, None, 0.96),
    ]
    btn_next = find_sign_in_button(ocr_next)
    assert btn_next is not None
    assert btn_next[0] == "Next"


@patch("app.browser_ocr_automation.pyautogui.hotkey")
@patch("app.browser_ocr_automation.pyautogui.click")
@patch("app.browser_ocr_automation.try_focus_antigravity")
def test_trigger_back_navigation(mock_focus, mock_click, mock_hotkey):
    # Case 1: Back button found in OCR
    ocr_with_back = [("Back", 120, 80, None, 0.95)]
    trigger_back_navigation(ocr_with_back)
    mock_click.assert_called_with(120, 80)
    mock_focus.assert_called()

    # Case 2: No back button on screen -> press Alt+Left
    mock_click.reset_mock()
    trigger_back_navigation([])
    mock_hotkey.assert_called_with("alt", "left")


@patch("app.browser_ocr_automation.capture_and_ocr")
@patch("app.browser_ocr_automation.try_focus_browser")
@patch("app.browser_ocr_automation.trigger_back_navigation")
def test_phase3_detects_2fa_and_raises(mock_nav, mock_focus, mock_capture):
    # OCR returns 2FA device challenge
    mock_capture.return_value = (
        [("2-Step Verification", 500, 200, None, 0.95), ("Check your phone", 500, 250, None, 0.90)],
        None,
    )
    with pytest.raises(TwoFactorChallengeError):
        phase3_wait_and_click_sign_in(timeout=1.0, poll_interval=0.2, save_snapshots=False)
    mock_nav.assert_called_once()


@patch("app.browser_ocr_automation.phase1_wait_for_page")
@patch("app.browser_ocr_automation.phase2_find_and_click")
@patch("app.browser_ocr_automation.phase3_wait_and_click_sign_in")
def test_run_browser_account_selection_handles_2fa(mock_p3, mock_p2, mock_p1):
    mock_p1.return_value = True
    mock_p2.return_value = True
    mock_p3.side_effect = TwoFactorChallengeError("Google 2FA challenge encountered.")

    res = run_browser_account_selection(
        target_email="challenged@gmail.com",
        save_snapshots=False,
    )

    assert res["success"] is False
    assert res["is_2fa_challenge"] is True
    assert "2FA challenge" in res["error"]


def test_detect_auth_success_screen():
    # Positive cases
    ocr_success1 = [("Authentication successful. You may close this tab.", 500, 300, None, 0.99)]
    assert detect_auth_success_screen(ocr_success1) is True

    ocr_success2 = [("Antigravity - Authentication complete", 500, 300, None, 0.98)]
    assert detect_auth_success_screen(ocr_success2) is True

    ocr_success3 = [("You're all set", 500, 300, None, 0.95)]
    assert detect_auth_success_screen(ocr_success3) is True

    # Negative cases
    ocr_neg = [("Choose an account", 500, 200, None, 0.95), ("user@gmail.com", 500, 300, None, 0.98)]
    assert detect_auth_success_screen(ocr_neg) is False


def test_phase3_early_completion_callback_bypasses_timeout():
    """
    When early_completion_checker returns True (e.g. Antigravity DOM already transitioned to Finish/chat),
    Phase 3 must return True immediately without waiting for timeout or clicking any buttons.
    """
    checker = MagicMock(return_value=True)
    res = phase3_wait_and_click_sign_in(
        timeout=1.0,
        poll_interval=0.1,
        early_completion_checker=checker,
        save_snapshots=False,
    )
    assert res is True
    checker.assert_called()


@patch("app.browser_ocr_automation.capture_and_ocr")
@patch("app.browser_ocr_automation.try_focus_browser")
def test_phase3_detects_auth_success_screen_and_returns_true(mock_focus, mock_capture):
    """
    When OCR finds an authentication success/redirect confirmation screen on the browser,
    Phase 3 must return True immediately.
    """
    mock_capture.return_value = (
        [("Authentication successful", 500, 300, None, 0.98)],
        None,
    )
    res = phase3_wait_and_click_sign_in(
        timeout=1.0,
        poll_interval=0.1,
        save_snapshots=False,
    )
    assert res is True


@patch("app.browser_ocr_automation.phase1_wait_for_page")
@patch("app.browser_ocr_automation.phase2_find_and_click")
@patch("app.browser_ocr_automation.phase3_wait_and_click_sign_in")
def test_run_browser_account_selection_forwards_early_completion_checker(mock_p3, mock_p2, mock_p1):
    mock_p1.return_value = True
    mock_p2.return_value = True
    mock_p3.return_value = True

    checker = MagicMock(return_value=True)
    res = run_browser_account_selection(
        target_email="test@gmail.com",
        save_snapshots=False,
        early_completion_checker=checker,
    )

    assert res["success"] is True
    mock_p3.assert_called_once()
    _, kwargs = mock_p3.call_args
    assert kwargs.get("early_completion_checker") == checker


def test_get_screen_virtual_offset_single_monitor():
    # When virtual screen starts at (0, 0)
    with patch("ctypes.windll.user32.GetSystemMetrics") as mock_metrics:
        # metrics: SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
        def fake_metrics(code):
            mapping = {76: 0, 77: 0, 78: 1920, 79: 1080}
            return mapping.get(code, 0)
        mock_metrics.side_effect = fake_metrics

        vx, vy = get_screen_virtual_offset()
        assert vx == 0
        assert vy == 0


def test_get_screen_virtual_offset_multi_monitor():
    # Secondary monitor placed to the left with negative coordinate origin
    with patch("ctypes.windll.user32.GetSystemMetrics") as mock_metrics:
        def fake_metrics(code):
            mapping = {76: -1920, 77: 0, 78: 3840, 79: 1080}
            return mapping.get(code, 0)
        mock_metrics.side_effect = fake_metrics

        vx, vy = get_screen_virtual_offset()
        assert vx == -1920
        assert vy == 0


def test_safe_click_catches_failsafe_exception():
    import pyautogui
    with patch("app.browser_ocr_automation.pyautogui.click", side_effect=pyautogui.FailSafeException("Fail-safe")):
        log_msgs = []
        ok = safe_click(0, 0, log_callback=lambda lvl, msg: log_msgs.append((lvl, msg)))
        assert ok is False
        assert any("fail-safe" in msg.lower() for _, msg in log_msgs)


def test_safe_move_to_catches_failsafe_exception():
    import pyautogui
    with patch("app.browser_ocr_automation.pyautogui.moveTo", side_effect=pyautogui.FailSafeException("Fail-safe")):
        log_msgs = []
        ok = safe_move_to(0, 0, log_callback=lambda lvl, msg: log_msgs.append((lvl, msg)))
        assert ok is False
        assert any("fail-safe" in msg.lower() for _, msg in log_msgs)


def test_safe_scroll_catches_failsafe_exception():
    import pyautogui
    with patch("app.browser_ocr_automation.pyautogui.scroll", side_effect=pyautogui.FailSafeException("Fail-safe")):
        log_msgs = []
        ok = safe_scroll(120, log_callback=lambda lvl, msg: log_msgs.append((lvl, msg)))
        assert ok is False
        assert any("fail-safe" in msg.lower() for _, msg in log_msgs)


@patch("app.browser_ocr_automation.safe_move_to")
@patch("app.browser_ocr_automation.safe_scroll")
def test_scroll_in_page_multi_monitor_browser_window(mock_scroll, mock_move):
    mock_win = MagicMock()
    mock_win.title = "Google Accounts - Sign in"
    mock_win.left = -1920
    mock_win.top = 100
    mock_win.width = 1200
    mock_win.height = 800

    with patch("pygetwindow.getAllWindows", return_value=[mock_win]):
        scroll_in_page("down", scroll_clicks=2)

    # center should be left + width // 2 = -1920 + 600 = -1320
    # top + height // 2 = 100 + 400 = 500
    mock_move.assert_called_once_with(-1320, 500, log_callback=pytest.approx(mock_move.call_args[1]["log_callback"]))
    mock_scroll.assert_called_once_with(-240, log_callback=pytest.approx(mock_scroll.call_args[1]["log_callback"]))



