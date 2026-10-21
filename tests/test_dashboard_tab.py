import pytest
from PyQt6.QtWidgets import QApplication
from app.ui.dashboard_tab import DashboardTab
from app.ui.widgets import StatefulActionButton, CallableState


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_callable_state():
    state = CallableState("IDLE")
    assert state == "IDLE"
    assert state() == "IDLE"
    assert str(state) == "IDLE"
    assert isinstance(state, str)


def test_stateful_action_button(qapp):
    btn = StatefulActionButton()
    btn.register_state("IDLE", "Start Queue", icon="▶", variant="success")
    btn.register_state("RUNNING", "Pause Queue", icon="⏸", variant="warning")
    btn.register_state("PAUSED", "Resume Queue", icon="▶", variant="primary")

    btn.set_state("IDLE")
    assert btn.current_state == "IDLE"
    assert btn.current_state() == "IDLE"

    btn.set_state("RUNNING")
    assert btn.current_state == "RUNNING"
    assert btn.current_state() == "RUNNING"

    btn.set_state("PAUSED")
    assert btn.current_state == "PAUSED"
    assert btn.current_state() == "PAUSED"


def test_dashboard_tab_start_pause_toggle_signals(qapp):
    tab = DashboardTab()

    start_calls = []
    pause_calls = []
    resume_calls = []

    tab.sig_start_requested.connect(lambda: start_calls.append(True))
    tab.sig_pause_requested.connect(lambda: pause_calls.append(True))
    tab.sig_resume_requested.connect(lambda: resume_calls.append(True))

    # 1. When in IDLE, clicking should emit sig_start_requested
    assert tab.start_pause_btn.current_state == "IDLE"
    tab.start_pause_btn.click()
    assert len(start_calls) == 1
    assert len(pause_calls) == 0
    assert len(resume_calls) == 0

    # 2. When state transitions to RUNNING, clicking should emit sig_pause_requested
    tab.set_running_state("RUNNING")
    assert tab.start_pause_btn.current_state == "RUNNING"
    tab.start_pause_btn.click()
    assert len(start_calls) == 1
    assert len(pause_calls) == 1
    assert len(resume_calls) == 0

    # 3. When state transitions to PAUSED, clicking should emit sig_resume_requested
    tab.set_running_state("PAUSED")
    assert tab.start_pause_btn.current_state == "PAUSED"
    tab.start_pause_btn.click()
    assert len(start_calls) == 1
    assert len(pause_calls) == 1
    assert len(resume_calls) == 1


def test_main_window_start_pause_btn_integration(qapp):
    from app.ui.main_window import MainWindow

    win = MainWindow()
    called = []
    # Re-wire signal to capture call cleanly
    win.dashboard_tab.sig_start_requested.disconnect()
    win.dashboard_tab.sig_start_requested.connect(lambda: called.append(True))

    assert win.dashboard_tab.start_pause_btn.current_state == "IDLE"
    win.dashboard_tab.start_pause_btn.click()
    assert len(called) == 1

