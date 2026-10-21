"""Window and process helper utilities for Antigravity CDP."""

import os
import ctypes
from typing import Optional, Tuple
import pygetwindow as gw

BROWSER_EXES = (
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "firefox.exe",
    "opera.exe",
    "vivaldi.exe",
    "chromium.exe",
    "arc.exe",
)

IDE_EXES = (
    "code.exe",
    "cursor.exe",
    "windsurf.exe",
    "pycharm64.exe",
    "idea64.exe",
    "devenv.exe",
)

EXCLUDED_OWN_KEYWORDS = (
    "antigravity cdp",
    "cdp studio",
    "floating hud",
    "project hud",
    "automation",
)


def _get_hwnd_pid_and_exe(hwnd: int) -> Tuple[int, str]:
    """Returns (pid, lowercase_exe_name) for a Win32 window handle."""
    if not hwnd:
        return 0, ""
    try:
        user32 = ctypes.windll.user32
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        pid_val = int(pid.value)
        if not pid_val:
            return 0, ""
        import psutil
        return pid_val, psutil.Process(pid_val).name().lower()
    except Exception:
        return 0, ""


def is_white_logo_antigravity_window(hwnd: int) -> bool:
    """Returns True if the window belongs to the Antigravity Electron process (`antigravity.exe`)."""
    pid, exe = _get_hwnd_pid_and_exe(hwnd)
    if pid and pid == os.getpid():
        return False
    return exe == "antigravity.exe"


def is_ide_process(hwnd: int, title: str = "") -> bool:
    """Returns True if the window belongs to a third-party IDE editor."""
    _, exe = _get_hwnd_pid_and_exe(hwnd)
    if exe in IDE_EXES:
        return True
    t_lower = (title or "").lower()
    return "visual studio" in t_lower or "cursor" in t_lower or "ide" in t_lower


def is_browser_process(hwnd: int, title: str = "") -> bool:
    """Returns True if the window belongs to a web browser process."""
    _, exe = _get_hwnd_pid_and_exe(hwnd)
    if exe in BROWSER_EXES:
        return True
    t_lower = (title or "").lower()
    return any(b in t_lower for b in ("google chrome", "microsoft edge", "mozilla firefox", "brave"))


def find_antigravity_window(target_hwnd: int = 0):
    """
    Locates the target Antigravity IDE window.
    1. Honors explicit `target_hwnd` if provided and still valid.
    2. Prioritizes windows backed by `antigravity.exe` (excluding our own process and HUD/Studio windows).
    3. Falls back to title matching while excluding Antigravity CDP Studio, HUDs, and browsers.
    """
    try:
        windows = gw.getAllWindows()
    except Exception:
        return None

    own_pid = os.getpid()

    # 1. Explicit target_hwnd match
    if target_hwnd and int(target_hwnd) > 0:
        for w in windows:
            if getattr(w, "_hWnd", 0) == int(target_hwnd):
                return w

    # 2. Process-verified Antigravity window
    fallback_match = None
    for w in windows:
        t = (w.title or "").strip()
        if not t:
            continue
        t_lower = t.lower()
        if any(bad in t_lower for bad in EXCLUDED_OWN_KEYWORDS):
            continue
        hwnd = getattr(w, "_hWnd", 0)
        pid, exe = _get_hwnd_pid_and_exe(hwnd)
        if pid and pid == own_pid:
            continue
        if exe == "antigravity.exe":
            return w
        if (
            fallback_match is None
            and "antigravity" in t_lower
            and "studio" not in t_lower
            and "cdp" not in t_lower
            and "hud" not in t_lower
            and "ide" not in t_lower
            and exe not in BROWSER_EXES
        ):
            fallback_match = w

    return fallback_match


def force_activate_window(hwnd: int) -> bool:
    """
    Brings `hwnd` to the foreground without un-maximizing already maximized windows.
    Only calls `SW_RESTORE` (9) when `IsIconic(hwnd)` is True.
    """
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd_int = int(hwnd)
        if user32.GetForegroundWindow() == hwnd_int:
            return True
        if user32.IsIconic(hwnd_int):
            user32.ShowWindow(hwnd_int, 9)  # SW_RESTORE only when minimized

        fg_hwnd = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        cur_tid = kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            user32.BringWindowToTop(hwnd_int)
            user32.SetForegroundWindow(hwnd_int)
        finally:
            if attached:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
        return True
    except Exception:
        return False

