import sys
import ctypes
from ctypes import wintypes
from typing import Optional
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

try:
    import psutil
except ImportError:
    psutil = None


def get_window_title(hwnd: int) -> str:
    """Returns the title text of the given window handle on Windows."""
    if not hwnd or sys.platform != "win32":
        return ""
    try:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def get_window_process_name(hwnd: int) -> str:
    """Returns the executable name of the process that owns the given window handle."""
    if not hwnd or sys.platform != "win32" or not psutil:
        return ""
    try:
        user32 = ctypes.windll.user32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            proc = psutil.Process(pid.value)
            return proc.name()
    except Exception:
        pass
    return ""


def is_antigravity_window(hwnd: int, hud_hwnd: int = 0) -> bool:
    """
    Determines whether the given window is Google Antigravity or the HUD itself.
    Distinguishes Google Antigravity from Antigravity IDE and Studio windows.
    """
    if not hwnd:
        return False

    # 1. Self-exemption: if the user clicks on the HUD itself
    if hud_hwnd and int(hwnd) == int(hud_hwnd):
        return True

    # 2. Process executable name matching
    proc_name = get_window_process_name(hwnd).lower()
    if proc_name:
        if "antigravity.exe" in proc_name and "ide" not in proc_name:
            return True
        if "ide" in proc_name:
            return False

    # 3. Window title matching fallback
    title = get_window_title(hwnd).lower()
    if title:
        # Reject Studio main window, IDE, or terminal
        if "antigravity cdp studio" in title or "antigravity ide" in title:
            return False
        if "antigravity" in title:
            return True

    return False


class ForegroundWatcher(QObject):
    """
    Monitors the Windows foreground window at a lightweight interval (200-250ms).
    Emits sig_antigravity_focus_changed(True) when Google Antigravity or HUD is in foreground;
    Emits sig_antigravity_focus_changed(False) when another window or desktop is active.
    """

    sig_antigravity_focus_changed = pyqtSignal(bool)

    def __init__(self, parent=None, hud_hwnd: int = 0, poll_interval_ms: int = 250):
        super().__init__(parent)
        self.hud_hwnd: int = hud_hwnd
        self.poll_interval_ms: int = poll_interval_ms
        self._is_antigravity_focused: Optional[bool] = None

        self._timer = QTimer(self)
        self._timer.setInterval(self.poll_interval_ms)
        self._timer.timeout.connect(self.check_foreground)

    def set_hud_hwnd(self, hwnd: int):
        self.hud_hwnd = hwnd

    def start(self):
        self._timer.start()
        self.check_foreground()

    def stop(self):
        self._timer.stop()
        self._is_antigravity_focused = None

    def is_running(self) -> bool:
        return self._timer.isActive()

    def check_foreground(self):
        if sys.platform != "win32":
            return

        try:
            user32 = ctypes.windll.user32
            fg_hwnd = user32.GetForegroundWindow()
            is_ag = is_antigravity_window(fg_hwnd, hud_hwnd=self.hud_hwnd)

            if is_ag != self._is_antigravity_focused:
                self._is_antigravity_focused = is_ag
                self.sig_antigravity_focus_changed.emit(is_ag)
        except Exception:
            pass
