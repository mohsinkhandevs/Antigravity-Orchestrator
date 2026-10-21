"""
Windows Power & Sleep Prevention Manager
========================================
Prevents Windows from entering sleep, hibernate, or turning off the display
while Antigravity CDP automation is actively running.

Root Cause Solved:
Windows monitors physical keyboard and mouse hardware interrupts. Because CDP
and worker threads execute programmatically in the background without physical
input, Windows Power Manager assumes the machine is unattended and idle,
triggering:
- Event 42: "Hibernate from Sleep - Fixed Timeout"
- Event 109 & 577: Scheduled Windows Update idle reboots
- Event 105: Power source changes triggering DC sleep timeouts

Solution:
Uses Windows Kernel32 `SetThreadExecutionState` with a 30-second heartbeat thread
to assert `ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED | ES_CONTINUOUS`.
Gracefully releases power assertions when automation finishes, is paused, or exits.
"""

import sys
import time
import atexit
import ctypes
import threading
from typing import Optional, Callable

# Windows Kernel32 Execution State Flags
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_USER_PRESENT = 0x00000004
ES_AWAYMODE_REQUIRED = 0x00000040
ES_CONTINUOUS = 0x80000000


class WindowsPowerManager:
    """
    Manages Windows system execution state to keep laptop awake during automation.
    Thread-safe and cross-platform compatible (no-ops gracefully on non-Windows).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._is_active = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()
        self._is_windows = (sys.platform == "win32")
        self._log_callback: Optional[Callable[[str, str], None]] = None

        # Cleanly restore standard power management if Python exits
        atexit.register(self.release)

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    def set_log_callback(self, callback: Optional[Callable[[str, str], None]]):
        self._log_callback = callback

    def _log(self, level: str, message: str):
        if self._log_callback:
            try:
                self._log_callback(level, message)
            except Exception:
                pass

    def acquire(
        self,
        prevent_display_sleep: bool = True,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ) -> bool:
        """
        Asserts Windows execution state to prevent system sleep and display timeout.
        Starts a 30-second heartbeat thread to refresh the idle timer.
        """
        if log_callback:
            self._log_callback = log_callback

        with self._lock:
            if self._is_active:
                return True

            if not self._is_windows:
                self._is_active = True
                self._log("DEBUG", "[PowerManager] Non-Windows platform; awake lock simulated.")
                return True

            try:
                kernel32 = ctypes.windll.kernel32
                try:
                    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
                    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
                except Exception:
                    pass
                flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                if prevent_display_sleep:
                    flags |= ES_DISPLAY_REQUIRED

                prev_state = kernel32.SetThreadExecutionState(flags)
                if prev_state == 0:
                    self._log("WARN", "[PowerManager] SetThreadExecutionState returned 0 (failed to set execution state).")
                    return False

                self._is_active = True
                self._stop_heartbeat.clear()
                self._heartbeat_thread = threading.Thread(
                    target=self._heartbeat_loop,
                    args=(prevent_display_sleep,),
                    daemon=True,
                    name="AntigravityPowerHeartbeat",
                )
                self._heartbeat_thread.start()

                self._log(
                    "SUCCESS",
                    "⚡ [PowerManager] Laptop Sleep & Display prevention activated. System will stay awake while automation runs.",
                )
                return True

            except Exception as e:
                self._log("WARN", f"[PowerManager] Could not set Windows execution state: {e}")
                return False

    def release(self) -> bool:
        """
        Releases execution state assertion and returns Windows to standard power management.
        """
        with self._lock:
            if not self._is_active:
                return True

            self._stop_heartbeat.set()
            self._is_active = False

            if not self._is_windows:
                return True

            try:
                kernel32 = ctypes.windll.kernel32
                try:
                    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
                    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
                except Exception:
                    pass
                # ES_CONTINUOUS alone resets the thread execution state back to default
                kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                self._log("INFO", "💤 [PowerManager] Released sleep prevention lock. Normal Windows power management restored.")
                return True
            except Exception as e:
                self._log("DEBUG", f"[PowerManager] Could not release execution state: {e}")
                return False

    def _heartbeat_loop(self, prevent_display_sleep: bool):
        """
        Heartbeat loop that periodically resets the idle timer every 30 seconds
        and asserts/releases ES_CONTINUOUS on the dedicated heartbeat thread.
        """
        try:
            while not self._stop_heartbeat.wait(timeout=30.0):
                if not self._is_active:
                    break
                try:
                    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                    if prevent_display_sleep:
                        flags |= ES_DISPLAY_REQUIRED
                    ctypes.windll.kernel32.SetThreadExecutionState(flags)
                except Exception:
                    pass
        finally:
            if self._is_windows:
                try:
                    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                except Exception:
                    pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


# Global power manager singleton
power_manager = WindowsPowerManager()
