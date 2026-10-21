from unittest.mock import patch, MagicMock
import pytest
from PyQt6.QtWidgets import QApplication

from app.config import config
from app.models import PromptItem, TaskStatus
from app.ui.foreground_watcher import is_antigravity_window, ForegroundWatcher
from app.ui.project_hud import ForegroundProjectHUD


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_is_antigravity_window_matching():
    # 1. Null / zero hwnd
    assert is_antigravity_window(0) is False

    # 2. Self exemption: clicking on the HUD itself
    assert is_antigravity_window(12345, hud_hwnd=12345) is True

    # 3. Process name Antigravity.exe
    with patch("app.ui.foreground_watcher.get_window_process_name", return_value="Antigravity.exe"), \
         patch("app.ui.foreground_watcher.get_window_title", return_value=""):
        assert is_antigravity_window(999) is True

    # 4. Process name Antigravity IDE.exe (must reject)
    with patch("app.ui.foreground_watcher.get_window_process_name", return_value="Antigravity IDE.exe"), \
         patch("app.ui.foreground_watcher.get_window_title", return_value=""):
        assert is_antigravity_window(999) is False

    # 5. Window title fallback: Antigravity window
    with patch("app.ui.foreground_watcher.get_window_process_name", return_value=""), \
         patch("app.ui.foreground_watcher.get_window_title", return_value="Google Antigravity"):
        assert is_antigravity_window(999) is True

    # 6. Window title: Studio main window (must reject)
    with patch("app.ui.foreground_watcher.get_window_process_name", return_value=""), \
         patch("app.ui.foreground_watcher.get_window_title", return_value="Studio — Antigravity CDP Studio"):
        assert is_antigravity_window(999) is False

    # 7. Unrelated window: Notepad
    with patch("app.ui.foreground_watcher.get_window_process_name", return_value="notepad.exe"), \
         patch("app.ui.foreground_watcher.get_window_title", return_value="Untitled - Notepad"):
        assert is_antigravity_window(999) is False


def test_foreground_watcher_lifecycle(qapp):
    watcher = ForegroundWatcher(hud_hwnd=5555, poll_interval_ms=100)
    assert watcher.is_running() is False

    watcher.start()
    assert watcher.is_running() is True

    watcher.stop()
    assert watcher.is_running() is False


def test_foreground_watcher_state_transitions(qapp):
    watcher = ForegroundWatcher(hud_hwnd=5555, poll_interval_ms=100)
    transitions = []
    watcher.sig_antigravity_focus_changed.connect(lambda state: transitions.append(state))

    with patch("app.ui.foreground_watcher.sys.platform", "win32"), \
         patch("app.ui.foreground_watcher.is_antigravity_window") as mock_is_ag, \
         patch("ctypes.windll.user32.GetForegroundWindow", return_value=123):

        # 1. Antigravity becomes foreground
        mock_is_ag.return_value = True
        watcher.check_foreground()
        assert transitions == [True]

        # 2. No change on duplicate check
        watcher.check_foreground()
        assert transitions == [True]

        # 3. User switches to Chrome (not Antigravity)
        mock_is_ag.return_value = False
        watcher.check_foreground()
        assert transitions == [True, False]

        # 4. User switches back to Antigravity
        mock_is_ag.return_value = True
        watcher.check_foreground()
        assert transitions == [True, False, True]


def test_foreground_project_hud_cards_and_signals(qapp):
    hud = ForegroundProjectHUD()

    # Initial state: placeholder is in layout
    assert hud.cards_layout.count() == 1
    assert hud.placeholder_lbl.text() == "No active projects running."

    # Update with multiple running projects including IDLE, STABILIZING, COMPLETED, DISPATCHING
    hud.update_projects({
        "Agrico-V2": {
            "status": "GENERATING",
            "elapsed_time": 25.0,
            "is_paused": False,
            "current_prompt_text": "/goal Implement dashboard page",
        },
        "Antigravity CDP": {
            "status": "PAUSED",
            "elapsed_time": 10.0,
            "is_paused": True,
            "current_prompt_text": "/plan Create test suite",
        },
        "2 step automation": {
            "status": "HALTED_QUOTA",
            "elapsed_time": 15.0,
            "is_paused": False,
            "current_prompt_text": "Run automated workflows",
        },
        "Project Idle": {
            "status": "IDLE",
            "elapsed_time": 0.0,
            "is_paused": False,
            "current_prompt_text": "Idle project prompt",
        },
        "Project Stabilizing": {
            "status": "STABILIZING",
            "elapsed_time": 3.0,
            "is_paused": False,
            "current_prompt_text": "Stabilizing prompt",
        },
        "Project Completed": {
            "status": "COMPLETED",
            "elapsed_time": 42.0,
            "is_paused": False,
            "current_prompt_text": "Completed prompt",
        },
        "Project Dispatching": {
            "status": "DISPATCHING",
            "elapsed_time": 1.0,
            "is_paused": False,
            "current_prompt_text": "Dispatching prompt",
        },
    })

    # Should have 7 cards rendered without any exception
    assert hud.cards_layout.count() == 7

    # Test clearing back to empty
    hud.update_projects({})
    assert hud.cards_layout.count() == 1
    assert hud.placeholder_lbl.text() == "No active projects running."

    # Test re-rendering idle alone (the exact startup state)
    hud.update_projects({"StartupProject": {"status": "IDLE"}})
    assert hud.cards_layout.count() == 1

    # Test pause and resume signals
    paused_projects = []
    resumed_projects = []
    hud.sig_pause_project_requested.connect(lambda p: paused_projects.append(p))
    hud.sig_resume_project_requested.connect(lambda p: resumed_projects.append(p))

    # Trigger pause on Agrico-V2
    hud.sig_pause_project_requested.emit("Agrico-V2")
    assert paused_projects == ["Agrico-V2"]

    # Trigger resume on Antigravity CDP
    hud.sig_resume_project_requested.emit("Antigravity CDP")
    assert resumed_projects == ["Antigravity CDP"]


def test_foreground_project_hud_hide_and_restore(qapp):
    hud = ForegroundProjectHUD()

    # Manual Hide button
    assert hud.is_manually_hidden is False
    hud.hide_hud()
    assert hud.is_manually_hidden is True
    assert hud.isVisible() is False

    # Restore from System Tray
    hud.show_hud()
    assert hud.is_manually_hidden is False
    assert hud.isVisible() is True


def test_main_window_foreground_hud_visibility_logic(qapp):
    from app.ui.main_window import MainWindow

    with patch.object(MainWindow, "_setup_emergency_hotkeys"), \
         patch.object(MainWindow, "_setup_tray_icon"):
        win = MainWindow()

    # Simulate worker running
    mock_worker = MagicMock()
    mock_worker.isRunning.return_value = True
    win.worker = mock_worker

    # 1. Antigravity in foreground -> HUD shows
    win._on_antigravity_focus_changed(True)
    assert win.hud.isVisible() is True

    # 2. Antigravity unfocused (user switches to Chrome) -> HUD hides
    win._on_antigravity_focus_changed(False)
    assert win.hud.isVisible() is False

    # 3. Antigravity in foreground again -> HUD reappears
    win._on_antigravity_focus_changed(True)
    assert win.hud.isVisible() is True

    # 4. User clicked [✕ Hide] on the HUD -> HUD hides and stays hidden even if Antigravity is active
    win.hud.hide_hud()
    assert win.hud.isVisible() is False
    win._on_antigravity_focus_changed(True)
    assert win.hud.isVisible() is False  # Stays hidden because manually hidden!

    # 5. User clicks "Show Live HUD" in System Tray -> HUD restored
    win.hud.show_hud()
    win._on_antigravity_focus_changed(True)
    assert win.hud.isVisible() is True

    # Cleanup
    win.hud.close()
    win.switch_countdown_hud.close()


def test_account_switch_countdown_hud_ui_and_change_now_signal(qapp):
    from PyQt6.QtCore import Qt
    from app.ui.project_hud import AccountSwitchCountdownHUD

    hud = AccountSwitchCountdownHUD()
    assert bool(hud.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert hud.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    # Start 30s countdown
    hud.start_countdown("user1@gmail.com", "user2@gmail.com", 30)
    assert hud.isVisible() is True
    assert "user1@gmail.com" in hud.lbl_account_info.text()
    assert "user2@gmail.com" in hud.lbl_account_info.text()
    assert hud.lbl_timer_badge.text() == "⏳ 30s"
    assert hud.progress_bar.value() == 30

    # Tick down to 18s
    hud.update_countdown(18, 30)
    assert hud.lbl_timer_badge.text() == "⏳ 18s"
    assert hud.progress_bar.value() == 18

    # Click "⚡ Change Now" button -> hides HUD and emits signal
    clicked_events = []
    hud.sig_change_now_requested.connect(lambda: clicked_events.append(True))
    hud.btn_change_now.click()

    assert clicked_events == [True]
    assert hud.isVisible() is False
    hud.close()


def test_pre_switch_countdown_auto_proceeds_on_timeout(monkeypatch, qapp):
    import time
    from app.worker import AutomationWorker

    monkeypatch.setattr(config, "enable_pre_switch_hud", True)
    monkeypatch.setattr(config, "pre_switch_countdown_seconds", 1)
    worker = AutomationWorker(queue=[])

    started_events = []
    finished_events = []
    worker.sig_pre_switch_countdown_started.connect(lambda c, t, s: started_events.append((c, t, s)))
    worker.sig_pre_switch_countdown_finished.connect(lambda: finished_events.append(True))

    t0 = time.time()
    proceed = worker._wait_for_pre_switch_countdown("a@gmail.com", "b@gmail.com")
    elapsed = time.time() - t0

    assert proceed is True
    assert elapsed >= 0.85
    assert started_events == [("a@gmail.com", "b@gmail.com", 1)]
    assert finished_events == [True]


def test_pre_switch_countdown_immediate_on_change_now(monkeypatch, qapp):
    import time
    import threading
    from app.worker import AutomationWorker

    monkeypatch.setattr(config, "enable_pre_switch_hud", True)
    monkeypatch.setattr(config, "pre_switch_countdown_seconds", 30)
    worker = AutomationWorker(queue=[])

    # Trigger "Change Now" 0.15s after countdown begins
    threading.Timer(0.15, worker.trigger_switch_now).start()

    t0 = time.time()
    proceed = worker._wait_for_pre_switch_countdown("a@gmail.com", "b@gmail.com")
    elapsed = time.time() - t0

    assert proceed is True
    assert elapsed < 1.5  # Unblocked immediately instead of waiting 30s!


def test_pre_switch_countdown_disabled_skips_hud_and_proceeds_immediately(monkeypatch, qapp):
    import time
    from app.worker import AutomationWorker

    monkeypatch.setattr(config, "enable_pre_switch_hud", False)
    monkeypatch.setattr(config, "pre_switch_countdown_seconds", 30)
    worker = AutomationWorker(queue=[])

    started_events = []
    worker.sig_pre_switch_countdown_started.connect(lambda c, t, s: started_events.append((c, t, s)))

    t0 = time.time()
    proceed = worker._wait_for_pre_switch_countdown("a@gmail.com", "b@gmail.com")
    elapsed = time.time() - t0

    assert proceed is True
    assert elapsed < 0.1
    assert started_events == []  # HUD was not shown when setting is disabled!


def test_settings_tab_pre_switch_hud_toggle_and_timer(monkeypatch, qapp):
    from PyQt6.QtWidgets import QMessageBox
    from app.ui.settings_tab import SettingsTab

    monkeypatch.setattr(config, "enable_pre_switch_hud", True)
    monkeypatch.setattr(config, "pre_switch_countdown_seconds", 30)
    monkeypatch.setattr(config, "save", lambda *a, **kw: "config.json")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: None)

    with patch.object(SettingsTab, "_test_cdp_connection"):
        tab = SettingsTab()

    assert tab.chk_pre_switch_hud.isChecked() is True
    assert tab.spin_pre_switch_seconds.value() == 30
    assert tab.spin_pre_switch_seconds.isEnabled() is True

    # Disable HUD checkbox -> spinbox disables and saves False
    tab.chk_pre_switch_hud.setChecked(False)
    assert tab.spin_pre_switch_seconds.isEnabled() is False
    tab._save_settings()
    assert config.enable_pre_switch_hud is False

    # Re-enable HUD checkbox and set custom timer -> saves True and custom seconds
    tab.chk_pre_switch_hud.setChecked(True)
    assert tab.spin_pre_switch_seconds.isEnabled() is True
    tab.spin_pre_switch_seconds.setValue(45)
    tab._save_settings()
    assert config.enable_pre_switch_hud is True
    assert config.pre_switch_countdown_seconds == 45
    tab.close()


def test_browser_oauth_pre_continue_callback_order(monkeypatch):
    from cdp.browser_oauth import BrowserOAuthHandler
    from app.account_manager import account_manager, GoogleAccount

    original_accounts = list(account_manager.accounts)
    original_current = account_manager.current_email
    monkeypatch.setattr(account_manager, "save_accounts", lambda: None)
    monkeypatch.setattr(
        account_manager,
        "accounts",
        [
            GoogleAccount(email="first@gmail.com", enabled=True),
            GoogleAccount(email="second@gmail.com", enabled=True),
        ],
    )

    call_order = []
    mock_page = MagicMock()
    handler = BrowserOAuthHandler(mock_page)

    monkeypatch.setattr(handler, "_open_profile_page", lambda log=None: (call_order.append("open_profile") or True))
    monkeypatch.setattr(handler, "_read_current_email", lambda log=None: (call_order.append("read_email") or "first@gmail.com"))
    monkeypatch.setattr(handler, "_click_sign_out", lambda log=None: (call_order.append("sign_out") or True))
    monkeypatch.setattr(
        handler,
        "_click_continue_with_google",
        lambda log=None: (call_order.append("continue_with_google") or True),
    )
    monkeypatch.setattr(handler, "_wait_and_click_finish", lambda *a, **kw: (call_order.append("finish") or True))
    monkeypatch.setattr(handler, "_close_settings_and_focus", lambda: True)
    monkeypatch.setattr(
        "cdp.browser_oauth.run_browser_account_selection",
        lambda **kw: (call_order.append("browser_ocr") or {"success": True}),
    )

    def pre_continue(curr, target):
        call_order.append(f"countdown_hud:{curr}->{target}")
        return True

    res = handler.execute_account_switch(
        target_account_email="second@gmail.com",
        pre_continue_callback=pre_continue,
    )

    assert res["success"] is True
    assert call_order == [
        "open_profile",
        "read_email",
        "sign_out",
        "countdown_hud:first@gmail.com->second@gmail.com",
        "continue_with_google",
        "browser_ocr",
        "finish",
    ]

    account_manager.accounts = original_accounts
    account_manager.current_email = original_current


def test_foreground_hud_fixed_height_and_scroll_area(qapp):
    from PyQt6.QtWidgets import QScrollArea
    from PyQt6.QtCore import Qt

    hud = ForegroundProjectHUD()

    # Verify fixed dimensions
    assert hud.width() == 380
    assert hud.height() == 360
    assert hud.minimumWidth() == 380
    assert hud.maximumWidth() == 380
    assert hud.minimumHeight() == 360
    assert hud.maximumHeight() == 360

    # Verify scroll area structure & policies
    assert hasattr(hud, "scroll_area")
    assert isinstance(hud.scroll_area, QScrollArea)
    assert hud.scroll_area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert hud.scroll_area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert hud.scroll_area.widgetResizable() is True
    assert hud.scroll_area.widget() == hud.cards_container
    assert hud.cards_container.layout() == hud.cards_layout

    # Populate 20 project queues
    many_projects = {
        f"Project_{i}": {
            "status": "GENERATING" if i % 2 == 0 else "PAUSED",
            "elapsed_time": float(i * 10),
            "is_paused": (i % 2 != 0),
            "current_prompt_text": f"Running large prompt queue number {i}",
            "pending_count": i,
        }
        for i in range(20)
    }

    hud.update_projects(many_projects)

    # Verify 20 cards exist
    assert hud.cards_layout.count() == 20

    # Verify that HUD height is strictly fixed and has NOT grown
    assert hud.height() == 360
    assert hud.width() == 380

    # Clear back to empty
    hud.update_projects({})
    assert hud.cards_layout.count() == 1
    assert hud.height() == 360
    assert hud.width() == 380

    hud.close()



