import sys
import time
import pytest
from unittest.mock import MagicMock, patch

from app.power_manager import (
    WindowsPowerManager,
    ES_CONTINUOUS,
    ES_SYSTEM_REQUIRED,
    ES_DISPLAY_REQUIRED,
)


def test_power_manager_initial_state():
    pm = WindowsPowerManager()
    assert not pm.is_active


def test_power_manager_acquire_and_release_mock():
    pm = WindowsPowerManager()
    logs = []
    pm.set_log_callback(lambda lvl, msg: logs.append((lvl, msg)))

    mock_kernel32 = MagicMock()
    mock_kernel32.SetThreadExecutionState.return_value = 0x80000001

    with patch("ctypes.windll", create=True) as mock_windll:
        mock_windll.kernel32 = mock_kernel32
        with patch.object(pm, "_is_windows", True):
            # Acquire
            success = pm.acquire(prevent_display_sleep=True)
            assert success is True
            assert pm.is_active is True
            assert mock_kernel32.SetThreadExecutionState.called

            # Check flags passed: ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            expected_flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            mock_kernel32.SetThreadExecutionState.assert_called_with(expected_flags)

            # Re-acquiring while active should be a no-op
            mock_kernel32.SetThreadExecutionState.reset_mock()
            assert pm.acquire() is True
            assert not mock_kernel32.SetThreadExecutionState.called

            # Release
            assert pm.release() is True
            assert not pm.is_active
            mock_kernel32.SetThreadExecutionState.assert_called_with(ES_CONTINUOUS)


def test_power_manager_context_manager():
    pm = WindowsPowerManager()
    mock_kernel32 = MagicMock()
    mock_kernel32.SetThreadExecutionState.return_value = 0x80000001

    with patch("ctypes.windll", create=True) as mock_windll:
        mock_windll.kernel32 = mock_kernel32
        with patch.object(pm, "_is_windows", True):
            with pm:
                assert pm.is_active is True
            assert not pm.is_active


def test_power_manager_non_windows_simulation():
    pm = WindowsPowerManager()
    with patch.object(pm, "_is_windows", False):
        assert pm.acquire() is True
        assert pm.is_active is True
        assert pm.release() is True
        assert not pm.is_active
