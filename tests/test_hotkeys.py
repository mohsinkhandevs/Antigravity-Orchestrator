"""
Unit tests for GlobalHotkeyManager and thread-safe Qt hotkey dispatching.
"""

from unittest.mock import MagicMock, patch
import pytest

from app.hotkeys import GlobalHotkeyManager, KEYBOARD_AVAILABLE, PYQT_AVAILABLE


def test_hotkey_manager_registration_and_unregister():
    mgr = GlobalHotkeyManager()

    # Mock keyboard module
    with patch("app.hotkeys.KEYBOARD_AVAILABLE", True), \
         patch("app.hotkeys.keyboard.add_hotkey", return_value="mock_hook") as mock_add, \
         patch("app.hotkeys.keyboard.remove_hotkey") as mock_remove:

        cb = MagicMock()
        ok = mgr.register("f10", cb, name="test_emergency")
        assert ok is True
        assert mgr.is_registered("test_emergency") is True
        mock_add.assert_called_once()

        # Unregister by name
        mgr.unregister("test_emergency")
        assert mgr.is_registered("test_emergency") is False
        mock_remove.assert_called_once()


def test_hotkey_manager_trigger():
    mgr = GlobalHotkeyManager()
    triggered = []

    def mock_cb():
        triggered.append(True)

    with patch("app.hotkeys.KEYBOARD_AVAILABLE", True), \
         patch("app.hotkeys.keyboard.add_hotkey") as mock_add:

        mgr.register("ctrl+shift+h", mock_cb, name="test_restore")
        # Trigger manually
        ok = mgr.trigger("test_restore")
        assert ok is True

        import time
        time.sleep(0.1)  # wait for thread callback
        assert len(triggered) == 1

        # Trigger non-existent
        assert mgr.trigger("non_existent") is False


@pytest.mark.skipif(not PYQT_AVAILABLE, reason="PyQt6 required for signal bridge test")
def test_hotkey_manager_qt_signal_bridge(qapp):
    mgr = GlobalHotkeyManager()
    assert mgr.signal_bridge is not None

    emitted_names = []
    mgr.signal_bridge.sig_triggered.connect(lambda name: emitted_names.append(name))

    with patch("app.hotkeys.KEYBOARD_AVAILABLE", True), \
         patch("app.hotkeys.keyboard.add_hotkey"):

        mgr.register("f10", lambda: None, name="emergency")
        mgr.trigger("emergency")

        qapp.processEvents()
        assert "emergency" in emitted_names


@pytest.mark.skipif(not PYQT_AVAILABLE, reason="PyQt6 required for MainWindow hotkey test")
def test_main_window_hotkey_queued_connections(qapp):
    from app.ui.main_window import MainWindow

    with patch.object(MainWindow, "_setup_tray_icon"):
        win = MainWindow()

    mock_worker = MagicMock()
    mock_worker.isRunning.return_value = True
    win.worker = mock_worker

    # Simulate emergency stop signal emitted from background thread
    win.sig_emergency_stop_requested.emit()
    qapp.processEvents()

    mock_worker.cancel.assert_called_once()
    assert "EMERGENCY STOP" in win.status_label.text()
