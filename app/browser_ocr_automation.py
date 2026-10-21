"""
Integrated Browser OCR Account Selection Automation
===================================================
Automates Google account selection and sign-in confirmation inside
the external system browser using RapidOCR and PyAutoGUI.

Workflow:
- Phase 1: Wait for Google "Choose an account" page to load (detect @gmail.com)
- Phase 2: Locate target email (scroll down, then up, with settle delay) and click
- Phase 3: Wait for confirmation page, detect standalone "Sign in" button, and click

Integrated natively into Antigravity CDP Studio with support for real-time
logging, thread cancellation, DPI awareness, and GDI BitBlt fallback.
"""

import os
import sys
import time
import ctypes
import threading
from datetime import datetime
from typing import Optional, Callable, Dict, Any, List, Tuple
from PIL import Image
import numpy as np
import pyautogui
from rapidocr_onnxruntime import RapidOCR

# ── Configuration Defaults ──────────────────────────────────────────────
DEFAULT_PAGE_LOAD_POLL_INTERVAL = 1.0     # seconds between page load checks
DEFAULT_SCROLL_SETTLE_DELAY = 0.5         # seconds to settle after scroll
DEFAULT_SIGN_IN_POLL_INTERVAL = 1.0       # seconds between sign-in button checks
DEFAULT_SCROLL_CLICKS = 3                 # mouse wheel notches per scroll
DEFAULT_PAGE_LOAD_TIMEOUT = 30.0          # max seconds to wait for account chooser
DEFAULT_SIGN_IN_PAGE_TIMEOUT = 12.0       # max seconds to wait for sign-in button
DEFAULT_ACCOUNT_SEARCH_MAX_SCROLLS = 10   # max scrolls down, then up
SNAPSHOTS_DIR = "snapshots"

# Ensure PyAutoGUI responsiveness
pyautogui.PAUSE = 0.05
pyautogui.FAILSAFE = False


class TwoFactorChallengeError(RuntimeError):
    """Raised when Google 2FA or device challenge is detected during sign in."""
    pass


class AccountNotInBrowserError(RuntimeError):
    """Raised when the target Google account is not present in the browser's account chooser list."""
    pass


class BrowserErrorPageException(RuntimeError):
    """Raised when the browser displays a Google 500/400 error page or network failure."""
    pass


BROWSER_EXE_NAMES = (
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "firefox.exe",
    "opera.exe",
    "vivaldi.exe",
    "chromium.exe",
    "arc.exe",
)

NON_BROWSER_TITLE_KEYWORDS = (
    "antigravity",
    "studio",
    "hud",
    "cursor",
    "vscode",
    "visual studio code",
    "windsurf",
)

TELEMETRY_NOISE_PATTERNS = (
    "status:",
    "switching to",
    "[info]",
    "[warn]",
    "[success]",
    "[error]",
    "click change",
    "antigravity cdp",
    "antigravity studio",
    "frozen until",
)


# ── Singleton Engine & Lock ─────────────────────────────────────────────
_ocr_engine_instance: Optional[RapidOCR] = None
_ocr_lock = threading.Lock()


def ensure_dpi_awareness():
    """Sets process DPI awareness on Windows so screen coordinates match physical pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


ensure_dpi_awareness()


def get_ocr_engine() -> RapidOCR:
    """Lazily loads and returns the shared RapidOCR engine singleton."""
    global _ocr_engine_instance
    with _ocr_lock:
        if _ocr_engine_instance is None:
            _ocr_engine_instance = RapidOCR()
    return _ocr_engine_instance


# ── Screen Capture with Multi-Monitor Engine (ImageGrab + PyAutoGUI + GDI Virtual Screen) ────

def get_screen_virtual_offset() -> Tuple[int, int]:
    """
    Returns (vx, vy) top-left coordinates of the virtual screen desktop on Windows.
    Returns (0, 0) on single monitor systems, non-Windows platforms, or when unavailable.
    """
    try:
        user32 = ctypes.windll.user32
        vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if vw > 0 and vh > 0:
            return int(vx), int(vy)
    except Exception:
        pass
    return 0, 0


def capture_screen_image() -> Image.Image:
    """
    Captures the virtual desktop across all connected monitors into a PIL Image.
    Uses ImageGrab.grab(all_screens=True) first, then pyautogui.screenshot(),
    with automatic fallback to Windows GDI BitBlt spanning virtual desktop
    metrics (SM_XVIRTUALSCREEN / SM_CXVIRTUALSCREEN) to guarantee reliable
    multi-monitor screen capture in all environments.
    """
    # 1. Try PIL ImageGrab with full multi-monitor virtual screen support
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
        if img:
            return img
    except Exception:
        pass

    # 2. Try PyAutoGUI screenshot
    try:
        img = pyautogui.screenshot()
        if img:
            return img
    except Exception:
        pass

    # 3. Fallback to Windows GDI BitBlt covering entire virtual desktop
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Virtual screen metrics (spans all monitors)
        vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

        # If virtual screen metrics are unavailable or invalid, fallback to primary display
        if vw <= 0 or vh <= 0:
            vx, vy = 0, 0
            vw = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            vh = user32.GetSystemMetrics(1)  # SM_CYSCREEN

        hdc = user32.GetDC(0)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, vw, vh)
        gdi32.SelectObject(mdc, bmp)
        gdi32.BitBlt(mdc, 0, 0, vw, vh, hdc, vx, vy, 0x00CC0020)  # SRCCOPY

        total_bytes = vw * vh * 4
        buffer = bytearray(total_bytes)
        gdi32.GetBitmapBits(bmp, total_bytes, (ctypes.c_char * total_bytes).from_buffer(buffer))

        # Cleanup GDI handles
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(0, hdc)

        img = Image.frombuffer("RGBA", (vw, vh), buffer, "raw", "BGRA", 0, 1).convert("RGB")
        return img
    except Exception as e:
        raise RuntimeError(f"All screen capture methods failed: {e}")


def capture_and_ocr(
    label: str,
    save_snapshots: bool = True,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Tuple[List[Tuple[str, int, int, Any, float]], Optional[str]]:
    """
    Captures full-screen snapshot across all monitors, saves to snapshots/<label>.png if enabled,
    and runs RapidOCR. Returns (parsed_ocr_results, saved_image_path).
    Each result item is a tuple: (text, center_x, center_y, box, confidence).
    Coordinates are translated by virtual screen offset so (cx, cy) match physical screen coordinates.
    """
    log = log_callback or (lambda lvl, m: None)
    saved_path = None

    try:
        img = capture_screen_image()
    except Exception as e:
        log("ERROR", f"Screen capture failed for '{label}': {e}")
        return [], None

    if save_snapshots:
        try:
            os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
            saved_path = os.path.join(SNAPSHOTS_DIR, f"{label}.png")
            img.save(saved_path)
        except Exception:
            saved_path = None

    vx, vy = get_screen_virtual_offset()

    try:
        engine = get_ocr_engine()
        img_np = np.array(img)
        raw_result, _ = engine(img_np)
        if raw_result:
            parsed = []
            for box, text, conf in raw_result:
                img_cx = int((box[0][0] + box[2][0]) / 2)
                img_cy = int((box[0][1] + box[2][1]) / 2)
                cx = img_cx + vx
                cy = img_cy + vy
                try:
                    conf_float = float(conf)
                except Exception:
                    conf_float = 0.0

                adjusted_box = box
                if isinstance(box, (list, tuple)):
                    try:
                        adjusted_box = [[pt[0] + vx, pt[1] + vy] for pt in box]
                    except Exception:
                        adjusted_box = box

                parsed.append((text, cx, cy, adjusted_box, conf_float))
            return parsed, saved_path
    except Exception as e:
        log("WARN", f"RapidOCR processing failed for '{label}': {e}")

    return [], saved_path


# ── Window Focus & Discovery Helpers ────────────────────────────────────

def get_browser_window_info(
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Locates the active or visible external browser window displaying Google Sign-In.
    Strictly filters by browser executable names via psutil (when available) and
    excludes Antigravity IDE / Antigravity CDP Studio windows so 'Google Antigravity'
    is never mistaken for a browser window.
    """
    log = log_callback or (lambda lvl, m: None)
    keywords = ("sign in", "google accounts", "choose an account", "google", "oauth", "accounts.google")

    # 1. Win32 EnumWindows + psutil executable check
    try:
        import psutil
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        candidates: List[Tuple[int, Dict[str, Any]]] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def enum_cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            title_lower = title.lower()

            if any(bad in title_lower for bad in NON_BROWSER_TITLE_KEYWORDS):
                return True

            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                return True

            exe_name = ""
            try:
                exe_name = psutil.Process(pid.value).name().lower()
            except Exception:
                pass

            is_browser_exe = exe_name in BROWSER_EXE_NAMES
            has_keyword = any(k in title_lower for k in keywords)
            if not is_browser_exe and not has_keyword:
                return True

            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < 150 or h < 150:
                return True

            score = 0
            if is_browser_exe:
                score += 10
            if any(k in title_lower for k in ("sign in", "google accounts", "choose an account")):
                score += 20
            elif "google" in title_lower:
                score += 5

            if score >= 10:
                candidates.append((
                    score,
                    {
                        "hwnd": hwnd,
                        "title": title,
                        "left": int(rect.left),
                        "top": int(rect.top),
                        "right": int(rect.right),
                        "bottom": int(rect.bottom),
                        "width": int(w),
                        "height": int(h),
                    },
                ))
            return True

        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]
    except Exception as e:
        log("DEBUG", f"Win32 browser discovery fallback notice: {e}")

    # 2. Fallback to pygetwindow with strict non-browser exclusion
    try:
        import pygetwindow as gw
        for w in gw.getAllWindows():
            title = w.title or ""
            title_lower = title.lower()
            if not title_lower or any(bad in title_lower for bad in NON_BROWSER_TITLE_KEYWORDS):
                continue
            if any(k in title_lower for k in keywords) and w.width > 150 and w.height > 150:
                return {
                    "hwnd": getattr(w, "_hWnd", 0),
                    "title": title,
                    "left": int(w.left),
                    "top": int(w.top),
                    "right": int(w.left + w.width),
                    "bottom": int(w.top + w.height),
                    "width": int(w.width),
                    "height": int(w.height),
                }
    except Exception as e:
        log("DEBUG", f"pygetwindow browser discovery notice: {e}")

    return None


def try_focus_browser(log_callback: Optional[Callable[[str, str], None]] = None) -> bool:
    """
    Attempts to bring the external browser window containing the Google sign-in page
    to the foreground without matching 'Google Antigravity' or restoring maximized windows
    to smaller sizes.
    """
    log = log_callback or (lambda lvl, m: None)
    info = get_browser_window_info(log_callback)
    if info and info.get("hwnd"):
        hwnd = info["hwnd"]
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if user32.GetForegroundWindow() == hwnd:
                return True
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE only if minimized
            fg_hwnd = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            cur_tid = kernel32.GetCurrentThreadId()
            attached = False
            if fg_tid and fg_tid != cur_tid:
                attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(cur_tid, fg_tid, False)
            log("INFO", f"Focused browser window: \"{info.get('title', '')[:60]}\"")
            return True
        except Exception as e:
            log("DEBUG", f"Win32 focus browser fallback notice: {e}")

    try:
        import pygetwindow as gw
        keywords = ["sign in", "google accounts", "choose an account", "google"]
        for w in gw.getAllWindows():
            title_lower = (w.title or "").lower()
            if any(bad in title_lower for bad in NON_BROWSER_TITLE_KEYWORDS):
                continue
            if any(k in title_lower for k in keywords):
                if not w.isActive:
                    try:
                        w.activate()
                        log("INFO", f"Focused browser window: \"{w.title[:60]}\"")
                    except Exception:
                        pass
                return True
    except Exception as e:
        log("DEBUG", f"Browser focus attempt notice: {e}")
    return False


def try_focus_antigravity(log_callback: Optional[Callable[[str, str], None]] = None) -> bool:
    """Attempts to bring Google Antigravity IDE window back to the foreground."""
    log = log_callback or (lambda lvl, m: None)
    try:
        import pygetwindow as gw
        keywords = ["antigravity", "cursor", "vscode"]
        exclude = ("cdp", "studio", "hud", "automation")
        for w in gw.getAllWindows():
            title_lower = (w.title or "").lower()
            if any(ex in title_lower for ex in exclude):
                continue
            if any(k in title_lower for k in keywords):
                if not w.isActive:
                    try:
                        w.activate()
                        log("INFO", f"Focused Antigravity window: \"{w.title[:60]}\"")
                    except Exception:
                        pass
                return True
    except Exception as e:
        log("DEBUG", f"Antigravity focus attempt notice: {e}")
    return False


# ── Text & Element Matching Logic ───────────────────────────────────────

CHALLENGE_KEYWORDS = [
    "2-step verification",
    "2-step",
    "verify it's you",
    "check your phone",
    "device prompt",
    "tap yes",
    "enter code",
    "try another way",
    "google prompt",
    "challenge",
    "challenge/dp",
    "confirm it's you",
    "security key",
]

ERROR_PAGE_KEYWORDS = (
    "500. that's an error",
    "400. that's an error",
    "there was an error",
    "please try again later",
    "that's all we know",
    "this site can't be reached",
    "err_connection",
    "err_timed_out",
    "server error",
)


def detect_2fa_challenge(ocr_results: List[Tuple]) -> bool:
    """Detects whether the current screen contains Google 2FA or device challenge prompts."""
    for text, _, _, _, _ in ocr_results:
        t = text.lower()
        for kw in CHALLENGE_KEYWORDS:
            if kw in t:
                return True
    return False


def detect_browser_error_page(ocr_results: List[Tuple]) -> Optional[str]:
    """
    Detects whether the browser is showing a Google 500/400 error page or network error.
    Returns the matched error description if detected, or None.
    """
    joined = " | ".join(r[0].strip().lower() for r in ocr_results if r and r[0])
    for kw in ERROR_PAGE_KEYWORDS:
        if kw in joined:
            return f"Browser displayed error page matching '{kw}'"
    # Check combination of '500' + 'error' or 'try again later'
    if ("500" in joined or "400" in joined) and ("that's an error" in joined or "try again later" in joined):
        return "Browser displayed Google 500/400 error page"
    return None


def find_back_button(ocr_results: List[Tuple]) -> Optional[Tuple[str, int, int, Any]]:
    """Locates a visible 'Back', 'Cancel', or arrow button on screen."""
    for text, cx, cy, box, conf in ocr_results:
        clean = text.strip().lower()
        if clean in ("back", "cancel", "<", "←"):
            return (text, cx, cy, box)
        if clean.startswith("back ") or clean.endswith(" back"):
            return (text, cx, cy, box)
    return None


# ── PyAutoGUI Safe Interaction Helpers ─────────────────────────────────

def safe_click(
    x: int,
    y: int,
    clicks: int = 1,
    interval: float = 0.0,
    button: str = "primary",
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """
    Executes a PyAutoGUI click safely avoiding FailSafe crashes at screen corners
    and logging any interaction errors.
    """
    log = log_callback or (lambda lvl, m: None)
    try:
        pyautogui.FAILSAFE = False
        if clicks == 1 and interval == 0.0 and button == "primary":
            pyautogui.click(x, y)
        else:
            pyautogui.click(x, y, clicks=clicks, interval=interval, button=button)
        return True
    except pyautogui.FailSafeException as e:
        log("WARN", f"PyAutoGUI fail-safe prevented click at ({x}, {y}): {e}")
        return False
    except Exception as e:
        log("WARN", f"Failed to click at ({x}, {y}): {e}")
        return False


def safe_move_to(
    x: int,
    y: int,
    duration: float = 0.0,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """Safely moves cursor without triggering FailSafe crashes."""
    log = log_callback or (lambda lvl, m: None)
    try:
        pyautogui.FAILSAFE = False
        pyautogui.moveTo(x=x, y=y, duration=duration)
        return True
    except pyautogui.FailSafeException as e:
        log("WARN", f"PyAutoGUI fail-safe prevented moveTo ({x}, {y}): {e}")
        return False
    except Exception as e:
        log("WARN", f"Failed to moveTo ({x}, {y}): {e}")
        return False


def safe_scroll(
    amount: int,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """Safely scrolls mouse wheel without triggering FailSafe crashes."""
    log = log_callback or (lambda lvl, m: None)
    try:
        pyautogui.FAILSAFE = False
        pyautogui.scroll(amount)
        return True
    except pyautogui.FailSafeException as e:
        log("WARN", f"PyAutoGUI fail-safe prevented scroll: {e}")
        return False
    except Exception as e:
        log("WARN", f"Failed to scroll: {e}")
        return False


def trigger_back_navigation(
    ocr_results: Optional[List[Tuple]] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
):
    """
    Looks for a back button to click; falls back to Alt+Left (browser back)
    and re-focuses Antigravity to return control to the main tool.
    """
    log = log_callback or (lambda lvl, m: None)
    clicked = False
    if ocr_results:
        back_btn = find_back_button(ocr_results)
        if back_btn:
            text, cx, cy, _ = back_btn
            log("INFO", f"🔙 Found '{text}' button at ({cx}, {cy}). Clicking back...")
            safe_click(cx, cy, log_callback=log)
            clicked = True
            time.sleep(0.5)

    if not clicked:
        log("INFO", "🔙 Pressing Alt+Left to navigate back in browser history...")
        pyautogui.hotkey("alt", "left")
        time.sleep(0.6)

    # Bring Antigravity IDE back to foreground
    try_focus_antigravity(log)


def _is_telemetry_or_noise_line(text: str) -> bool:
    """
    Returns True if an OCR line belongs to Antigravity CDP Studio's own status bar,
    telemetry log console, or long sentence rather than a Google account item.
    """
    clean = (text or "").strip().lower()
    if not clean:
        return True
    if len(clean) > 68:
        return True
    if any(pat in clean for pat in TELEMETRY_NOISE_PATTERNS):
        return True
    return False


def filter_ocr_to_browser_content(
    ocr_results: List[Tuple],
    browser_rect: Optional[Dict[str, Any]] = None,
) -> List[Tuple]:
    """
    Filters OCR results to remove telemetry/status lines and optionally restricts
    coordinates to the browser's content region (excluding the top 65px tab/URL bar
    and outside windows).
    """
    non_noise = [r for r in ocr_results if not _is_telemetry_or_noise_line(r[0])]
    if not browser_rect or browser_rect.get("width", 0) < 200 or browser_rect.get("height", 0) < 200:
        return non_noise

    b_left = browser_rect["left"]
    b_top = browser_rect["top"] + 65  # Exclude top tab strip and address bar
    b_right = browser_rect["right"]
    b_bottom = browser_rect["bottom"]

    in_bounds = [
        r for r in non_noise
        if b_left <= r[1] <= b_right and b_top <= r[2] <= b_bottom
    ]
    # Only apply coordinate restriction if at least one item matched inside the browser rect
    return in_bounds if in_bounds else non_noise


def find_text_on_screen(ocr_results: List[Tuple], search_text: str) -> List[Tuple]:
    """Finds OCR results containing search_text (case-insensitive), excluding telemetry noise."""
    search_lower = search_text.lower()
    return [
        r for r in ocr_results
        if not _is_telemetry_or_noise_line(r[0]) and search_lower in r[0].lower()
    ]


def find_exact_email(
    ocr_results: List[Tuple],
    target_email: str,
    browser_rect: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, int, int, Any]]:
    """
    Matches target email with OCR results using a strict 4-tier matching strategy
    that prevents self-OCR poisoning (matching Antigravity CDP Studio's own log/status text)
    and prefix collisions between similar usernames (e.g. user785 vs user786):
    1. Exact string match
    2. Target is full substring in a short line (<= len(target) + 12)
    3. Exact username match before '@'
    4. Exact username token match when '@gmail.com' was truncated at edge (len > 8)
    """
    target_lower = target_email.lower().strip()
    username = target_lower.split("@")[0] if "@" in target_lower else target_lower
    candidates = filter_ocr_to_browser_content(ocr_results, browser_rect)

    # Tier 1: exact match
    for text, cx, cy, box, conf in candidates:
        if text.lower().strip() == target_lower:
            return (text, cx, cy, box)

    # Tier 2: target is full substring in a short detected line (not a long telemetry sentence)
    for text, cx, cy, box, conf in candidates:
        clean = text.lower().strip()
        if target_lower in clean and len(clean) <= len(target_lower) + 12:
            return (text, cx, cy, box)

    # Tier 3: exact username match before '@'
    for text, cx, cy, box, conf in candidates:
        clean = text.lower().strip()
        if "@" in clean:
            before_at = clean.split("@")[0].strip()
            # Take last token before '@' in case an avatar letter precedes the email
            cand_user = before_at.split()[-1] if before_at else ""
            if cand_user == username:
                return (text, cx, cy, box)

    # Tier 4: exact username match when '@' was cut off at edge
    for text, cx, cy, box, conf in candidates:
        clean = text.lower().strip()
        if len(username) > 8 and "@" not in clean:
            tokens = clean.split()
            if clean == username or (tokens and tokens[-1] == username):
                return (text, cx, cy, box)

    return None


def find_sign_in_button(ocr_results: List[Tuple]) -> Optional[Tuple[str, int, int, Any]]:
    """
    Locates the standalone 'Sign in', 'Continue', 'Allow', 'Agree', or 'Next' button
    on the confirmation/consent page.
    Filters out headers like 'Sign in with Google' or long sentences.
    """
    # 1. Exact standalone buttons
    standalone_targets = ("sign in", "signin", "continue", "allow", "agree", "next")
    for text, cx, cy, box, conf in ocr_results:
        clean = text.strip().lower()
        if clean in standalone_targets:
            return (text, cx, cy, box)

    # 2. Fallback: short phrase matching "sign in" or "continue"
    for text, cx, cy, box, conf in ocr_results:
        clean = text.strip().lower()
        if "sign in" in clean and len(clean) <= 10:
            if "with" not in clean and "google" not in clean:
                return (text, cx, cy, box)
        if "continue" in clean and len(clean) <= 12:
            if "with google" not in clean:
                return (text, cx, cy, box)

    return None


def detect_confirmation_page(ocr_results: List[Tuple]) -> bool:
    """
    Detects whether the browser is currently on the Google OAuth confirmation page
    ('Sign in to Antigravity' / 'Make sure that you downloaded this app from Google')
    rather than the 'Choose an account' list page.
    """
    lower_texts = [r[0].strip().lower() for r in ocr_results if r and r[0]]
    joined = " | ".join(lower_texts)
    if "choose an account" in joined or "use another account" in joined:
        return False
    if "make sure that you downloaded" in joined or "downloaded this app from google" in joined:
        return True
    if "sign in to antigravity" in joined and find_sign_in_button(ocr_results) is not None:
        return True
    if find_sign_in_button(ocr_results) is not None and any(t == "cancel" for t in lower_texts):
        return True
    return False


def detect_auth_success_screen(ocr_results: List[Tuple]) -> bool:
    """
    Detects whether the browser screen displays an OAuth completion or success indicator,
    e.g. when an account has already been consented and Google immediately redirects
    to the Antigravity local callback or displays a success confirmation.
    """
    success_indicators = (
        "authentication successful",
        "authentication complete",
        "sign-in successful",
        "sign in successful",
        "you may close this",
        "you can close this",
        "close this window",
        "close this tab",
        "you're all set",
        "all set",
        "logged in",
    )
    for text, cx, cy, box, conf in ocr_results:
        clean = text.strip().lower()
        if any(ind in clean for ind in success_indicators):
            return True
    return False


def scroll_in_page(
    direction: str = "down",
    scroll_clicks: int = DEFAULT_SCROLL_CLICKS,
    log_callback: Optional[Callable[[str, str], None]] = None,
):
    """
    Scrolls mouse wheel at the center of the active browser window or screen using Windows WHEEL_DELTA.
    Multi-monitor aware: targets the actual browser window rather than 'Google Antigravity'.
    """
    log = log_callback or (lambda lvl, m: None)
    center_x, center_y = None, None

    info = get_browser_window_info(log_callback)
    if info and info.get("width", 0) > 150 and info.get("height", 0) > 150:
        center_x = int(info["left"] + info["width"] // 2)
        center_y = int(info["top"] + info["height"] // 2)

    if center_x is None or center_y is None:
        try:
            screen_w, screen_h = pyautogui.size()
            center_x = int(screen_w // 2)
            center_y = int(screen_h // 2)
        except Exception:
            center_x, center_y = 500, 500

    safe_move_to(center_x, center_y, log_callback=log)

    wheel_delta = scroll_clicks * 120
    amount = -wheel_delta if direction == "down" else wheel_delta
    safe_scroll(amount, log_callback=log)
    log("INFO", f"📜 Scrolled {direction} ({scroll_clicks} notches) at ({center_x}, {center_y})")


def _get_email_y_signature(ocr_results: List[Tuple]) -> List[int]:
    """Returns sorted Y-coordinates of detected @gmail items for scroll-exhaustion detection."""
    emails = find_text_on_screen(ocr_results, "@gmail")
    return sorted(int(r[2]) for r in emails)


def _did_scroll_shift(prev_sig: List[int], curr_sig: List[int], threshold_px: int = 6) -> bool:
    """Returns True if email Y-coordinates shifted meaningfully between two scroll captures."""
    if not prev_sig or not curr_sig:
        return True
    if len(prev_sig) != len(curr_sig):
        return True
    return any(abs(a - b) > threshold_px for a, b in zip(prev_sig, curr_sig))


# ── Phases of Automation ────────────────────────────────────────────────

def phase1_wait_for_page(
    timeout: float = DEFAULT_PAGE_LOAD_TIMEOUT,
    poll_interval: float = DEFAULT_PAGE_LOAD_POLL_INTERVAL,
    log_callback: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    save_snapshots: bool = True,
) -> bool:
    """
    Phase 1: Polls until '@gmail.com', '@gmail', or the Google confirmation page is detected
    on screen via OCR, confirming the Google OAuth browser page is loaded.
    """
    log = log_callback or (lambda lvl, m: None)
    log("INFO", "📋 [Phase 1/3] Waiting for Google 'Choose an account' page to load...")

    attempts = max(1, int(timeout / poll_interval))
    for attempt in range(1, attempts + 1):
        if stop_event and stop_event.is_set():
            raise InterruptedError("Cancelled by user.")

        try_focus_browser(log_callback)
        browser_rect = get_browser_window_info(log_callback)
        ocr_results, _ = capture_and_ocr(f"load_check_{attempt}", save_snapshots, log_callback)

        err_page = detect_browser_error_page(ocr_results)
        if err_page:
            log("WARN", f"⚠️ {err_page} during Phase 1! Navigating back...")
            trigger_back_navigation(ocr_results, log_callback=log)
            raise BrowserErrorPageException(err_page)

        filtered = filter_ocr_to_browser_content(ocr_results, browser_rect)
        emails = find_text_on_screen(filtered, "@gmail.com") or find_text_on_screen(filtered, "@gmail")

        if emails or detect_confirmation_page(filtered):
            log("SUCCESS", f"✅ Google OAuth page loaded! Detected {len(emails)} email account(s).")
            return True

        log("INFO", f"⏳ Waiting for account chooser page ({attempt}/{attempts})...")
        time.sleep(poll_interval)

    capture_and_ocr("timeout_page_load", save_snapshots, log_callback)
    raise TimeoutError(
        f"Page did not load within {timeout}s. "
        "Make sure the Google 'Choose an account' page is visible on screen."
    )


def phase2_find_and_click(
    target_email: str,
    max_scrolls: int = DEFAULT_ACCOUNT_SEARCH_MAX_SCROLLS,
    settle_delay: float = DEFAULT_SCROLL_SETTLE_DELAY,
    scroll_clicks: int = DEFAULT_SCROLL_CLICKS,
    log_callback: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    save_snapshots: bool = True,
) -> bool:
    """
    Phase 2: Searches screen for target_email via OCR and clicks it.
    Handles confirmation-page recovery, browser region filtering, scroll exhaustion
    detection, and post-click verification.
    """
    log = log_callback or (lambda lvl, m: None)
    log("INFO", f"📋 [Phase 2/3] Searching for target account '{target_email}'...")

    try_focus_browser(log_callback)
    browser_rect = get_browser_window_info(log_callback)

    # 1. Check current view first
    ocr_results, _ = capture_and_ocr("phase2_initial_view", save_snapshots, log_callback)

    err_page = detect_browser_error_page(ocr_results)
    if err_page:
        log("WARN", f"⚠️ {err_page} during Phase 2! Navigating back...")
        trigger_back_navigation(ocr_results, log_callback=log)
        raise BrowserErrorPageException(err_page)

    # Check if browser is already on the Confirmation Page ("Make sure that you downloaded...")
    if detect_confirmation_page(ocr_results):
        match_on_confirm = find_exact_email(ocr_results, target_email, browser_rect)
        if match_on_confirm:
            log(
                "SUCCESS",
                f"🎯 Browser is already on the confirmation page for '{target_email}'! Proceeding directly to Phase 3.",
            )
            return True
        else:
            # Confirmation page is open for a DIFFERENT account — click the account pill or Cancel to return to list
            log("INFO", "🔄 Browser is on confirmation page for a different account. Returning to account chooser...")
            other_emails = find_text_on_screen(filter_ocr_to_browser_content(ocr_results, browser_rect), "@gmail")
            if other_emails:
                _, pill_x, pill_y, _, _ = other_emails[0]
                safe_click(pill_x, pill_y, log_callback=log)
            else:
                back_btn = find_back_button(ocr_results)
                if back_btn:
                    safe_click(back_btn[1], back_btn[2], log_callback=log)
                else:
                    pyautogui.hotkey("alt", "left")
            time.sleep(1.0)
            try:
                ocr_results, _ = capture_and_ocr("phase2_after_confirm_back", save_snapshots, log_callback)
            except Exception:
                pass

    match = find_exact_email(ocr_results, target_email, browser_rect)
    if match:
        text, cx, cy, _ = match
        log("SUCCESS", f"🎯 Target account found in view: \"{text}\" at ({cx}, {cy})")
        safe_click(cx, cy, log_callback=log)
        log("INFO", f"🖱️ Clicked target account at ({cx}, {cy}).")
        return True

    prev_sig = _get_email_y_signature(ocr_results)

    # 2. Scroll DOWN
    log("INFO", f"🔽 Target not in view. Scrolling DOWN (up to {max_scrolls} times)...")
    for i in range(1, max_scrolls + 1):
        if stop_event and stop_event.is_set():
            raise InterruptedError("Cancelled by user.")

        scroll_in_page("down", scroll_clicks, log_callback)
        time.sleep(settle_delay)

        ocr_results, _ = capture_and_ocr(f"phase2_scroll_down_{i}", save_snapshots, log_callback)
        err_page = detect_browser_error_page(ocr_results)
        if err_page:
            trigger_back_navigation(ocr_results, log_callback=log)
            raise BrowserErrorPageException(err_page)

        match = find_exact_email(ocr_results, target_email, browser_rect)
        if match:
            text, cx, cy, _ = match
            log("SUCCESS", f"🎯 Target account found after scroll down: \"{text}\" at ({cx}, {cy})")
            safe_click(cx, cy, log_callback=log)
            log("INFO", f"🖱️ Clicked target account at ({cx}, {cy}).")
            return True

        curr_sig = _get_email_y_signature(ocr_results)
        has_footer = bool(find_text_on_screen(ocr_results, "use another account"))
        if has_footer and not _did_scroll_shift(prev_sig, curr_sig):
            log("INFO", f"🛑 Reached bottom of account chooser list at scroll {i}/{max_scrolls}.")
            prev_sig = curr_sig
            break
        prev_sig = curr_sig

    # 3. Scroll UP (past original view)
    total_up_scrolls = max_scrolls * 2
    log("INFO", f"🔼 Target not found below. Scrolling UP (up to {total_up_scrolls} times)...")
    for i in range(1, total_up_scrolls + 1):
        if stop_event and stop_event.is_set():
            raise InterruptedError("Cancelled by user.")

        scroll_in_page("up", scroll_clicks, log_callback)
        time.sleep(settle_delay)

        ocr_results, _ = capture_and_ocr(f"phase2_scroll_up_{i}", save_snapshots, log_callback)
        err_page = detect_browser_error_page(ocr_results)
        if err_page:
            trigger_back_navigation(ocr_results, log_callback=log)
            raise BrowserErrorPageException(err_page)

        match = find_exact_email(ocr_results, target_email, browser_rect)
        if match:
            text, cx, cy, _ = match
            log("SUCCESS", f"🎯 Target account found after scroll up: \"{text}\" at ({cx}, {cy})")
            safe_click(cx, cy, log_callback=log)
            log("INFO", f"🖱️ Clicked target account at ({cx}, {cy}).")
            return True

        curr_sig = _get_email_y_signature(ocr_results)
        has_header = bool(find_text_on_screen(ocr_results, "choose an account"))
        if has_header and not _did_scroll_shift(prev_sig, curr_sig):
            log("INFO", f"🛑 Reached top of account chooser list at scroll {i}/{total_up_scrolls}.")
            break
        prev_sig = curr_sig

    capture_and_ocr("phase2_not_found", save_snapshots, log_callback)
    raise AccountNotInBrowserError(
        f"Could not locate '{target_email}' after scrolling down and up. "
        "Please verify that this account is signed into the browser."
    )


def phase3_wait_and_click_sign_in(
    timeout: float = DEFAULT_SIGN_IN_PAGE_TIMEOUT,
    poll_interval: float = DEFAULT_SIGN_IN_POLL_INTERVAL,
    log_callback: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    save_snapshots: bool = True,
    early_completion_checker: Optional[Callable[[], bool]] = None,
) -> bool:
    """
    Phase 3: Polls for the confirmation page to load, locates the standalone
    'Sign in' / 'Continue' button, clicks it, and verifies the click registered.
    If a Google 2FA device challenge is detected, triggers back-navigation to return
    control to Antigravity and raises TwoFactorChallengeError.
    """
    log = log_callback or (lambda lvl, m: None)
    log("INFO", "📋 [Phase 3/3] Waiting for confirmation page 'Sign in' / 'Continue' button...")

    # Immediate pre-poll check if Antigravity already finished or entered workspace
    if early_completion_checker:
        try:
            if early_completion_checker():
                log("SUCCESS", "✅ Antigravity completed OAuth handshake directly (already consented account)!")
                return True
        except Exception as e:
            log("DEBUG", f"early_completion_checker error on initial check: {e}")

    attempts = max(1, int(timeout / poll_interval))
    last_ocr_results = []
    for attempt in range(1, attempts + 1):
        if stop_event and stop_event.is_set():
            raise InterruptedError("Cancelled by user.")

        time.sleep(poll_interval)

        # Check early completion condition on every iteration
        if early_completion_checker:
            try:
                if early_completion_checker():
                    log("SUCCESS", "✅ Antigravity completed OAuth handshake directly (already consented account)!")
                    return True
            except Exception as e:
                log("DEBUG", f"early_completion_checker error: {e}")

        try_focus_browser(log_callback)
        browser_rect = get_browser_window_info(log_callback)
        ocr_results, _ = capture_and_ocr(f"phase3_check_{attempt}", save_snapshots, log_callback)
        last_ocr_results = ocr_results

        err_page = detect_browser_error_page(ocr_results)
        if err_page:
            log("WARN", f"⚠️ {err_page} during Phase 3! Navigating back...")
            trigger_back_navigation(ocr_results, log_callback=log)
            raise BrowserErrorPageException(err_page)

        # Check if browser displays success/redirect confirmation screen
        if detect_auth_success_screen(ocr_results):
            log("SUCCESS", "✅ Browser displays OAuth completion/success message! (already consented account)")
            return True

        filtered = filter_ocr_to_browser_content(ocr_results, browser_rect)
        btn = find_sign_in_button(filtered) or find_sign_in_button(ocr_results)
        if btn:
            text, cx, cy, _ = btn
            log("SUCCESS", f"✅ Found '{text}' button at ({cx}, {cy})!")
            try_focus_browser(log_callback)
            safe_click(cx, cy, log_callback=log)
            log("SUCCESS", f"🎉 Clicked '{text}' button — verifying browser transition...")

            # Post-click verification: ensure the first click didn't merely focus the browser window
            try:
                time.sleep(0.8)
                if early_completion_checker and early_completion_checker():
                    return True
                verify_ocr, _ = capture_and_ocr("phase3_post_click_verify", save_snapshots, log_callback)
                if detect_confirmation_page(verify_ocr):
                    retry_btn = find_sign_in_button(filter_ocr_to_browser_content(verify_ocr, browser_rect))
                    if retry_btn:
                        r_text, r_cx, r_cy, _ = retry_btn
                        log("INFO", f"🔁 Confirmation page still visible after initial click — re-clicking '{r_text}' at ({r_cx}, {r_cy})...")
                        safe_click(r_cx, r_cy, log_callback=log)
            except Exception:
                pass
            return True

        if detect_2fa_challenge(ocr_results):
            log("WARN", "⚠️ Google 2FA device challenge detected on screen ('challenge/dp' or prompt)!")
            trigger_back_navigation(ocr_results, log_callback=log)
            raise TwoFactorChallengeError("Google 2FA device challenge encountered.")

        log("INFO", f"⏳ Checking for 'Sign in' / confirmation button ({attempt}/{attempts})...")

    # Post-loop final check before raising TimeoutError
    if early_completion_checker:
        try:
            if early_completion_checker():
                log("SUCCESS", "✅ Antigravity completed OAuth handshake directly on final check!")
                return True
        except Exception:
            pass

    capture_and_ocr("phase3_timeout", save_snapshots, log_callback)
    if last_ocr_results and detect_2fa_challenge(last_ocr_results):
        log("WARN", "⚠️ Google 2FA device challenge detected on timeout!")
        trigger_back_navigation(last_ocr_results, log_callback=log)
        raise TwoFactorChallengeError("Google 2FA device challenge encountered on timeout.")

    raise TimeoutError(
        f"'Sign in' button not found within {timeout}s. "
        "The confirmation page may not have loaded or button text differs."
    )


# ── Master Orchestration Function ───────────────────────────────────────

def run_browser_account_selection(
    target_email: str,
    log_callback: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    save_snapshots: bool = True,
    early_completion_checker: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Main entry point for Browser OCR Account Selection.
    Executes Phase 1, Phase 2, and Phase 3 in sequence.
    Returns:
        {
            "success": bool,
            "is_2fa_challenge": bool,
            "account_not_in_browser": bool,
            "is_error_page": bool,
            "step": str,
            "error": str,
            "target_email": target_email,
            "duration_seconds": float,
        }
    """
    log = log_callback or (lambda lvl, m: None)
    t0 = time.time()
    result = {
        "success": False,
        "is_2fa_challenge": False,
        "account_not_in_browser": False,
        "is_error_page": False,
        "step": "start",
        "error": "",
        "target_email": target_email,
        "duration_seconds": 0.0,
    }

    log("INFO", "═" * 56)
    log("INFO", f"🚀 Starting Browser OCR Sign-In Automation (Target: '{target_email}')")
    log("INFO", "═" * 56)

    try:
        # Phase 1: Wait for Page to Load
        result["step"] = "phase1_page_load"
        phase1_wait_for_page(
            timeout=DEFAULT_PAGE_LOAD_TIMEOUT,
            poll_interval=DEFAULT_PAGE_LOAD_POLL_INTERVAL,
            log_callback=log,
            stop_event=stop_event,
            save_snapshots=save_snapshots,
        )

        # Phase 2: Find & Click Target Account
        result["step"] = "phase2_find_account"
        phase2_find_and_click(
            target_email=target_email,
            max_scrolls=DEFAULT_ACCOUNT_SEARCH_MAX_SCROLLS,
            settle_delay=DEFAULT_SCROLL_SETTLE_DELAY,
            scroll_clicks=DEFAULT_SCROLL_CLICKS,
            log_callback=log,
            stop_event=stop_event,
            save_snapshots=save_snapshots,
        )

        # Phase 3: Wait for Confirmation & Click Sign In
        result["step"] = "phase3_sign_in_button"
        phase3_wait_and_click_sign_in(
            timeout=DEFAULT_SIGN_IN_PAGE_TIMEOUT,
            poll_interval=DEFAULT_SIGN_IN_POLL_INTERVAL,
            log_callback=log,
            stop_event=stop_event,
            save_snapshots=save_snapshots,
            early_completion_checker=early_completion_checker,
        )

        result["success"] = True
        result["step"] = "completed"
        dur = time.time() - t0
        result["duration_seconds"] = dur
        log("SUCCESS", f"✅ Browser OCR sign-in automation succeeded in {dur:.1f}s.")
        return result

    except InterruptedError as e:
        dur = time.time() - t0
        result["duration_seconds"] = dur
        result["error"] = "Cancelled by user."
        log("WARN", f"⛔ Browser automation interrupted: {e}")
        return result

    except TwoFactorChallengeError as e:
        dur = time.time() - t0
        result["duration_seconds"] = dur
        result["is_2fa_challenge"] = True
        result["error"] = str(e)
        log("WARN", f"⚠️ 2FA Challenge detected for '{target_email}'. Navigated back to return control to main tool.")
        return result

    except AccountNotInBrowserError as e:
        dur = time.time() - t0
        result["duration_seconds"] = dur
        result["account_not_in_browser"] = True
        result["error"] = str(e)
        log("WARN", f"⚠️ Target account '{target_email}' not present in browser list: {e}")
        trigger_back_navigation(log_callback=log)
        return result

    except BrowserErrorPageException as e:
        dur = time.time() - t0
        result["duration_seconds"] = dur
        result["is_error_page"] = True
        result["error"] = str(e)
        log("WARN", f"⚠️ Browser error page encountered for '{target_email}': {e}")
        return result

    except (TimeoutError, RuntimeError) as e:
        dur = time.time() - t0
        result["duration_seconds"] = dur
        result["error"] = str(e)
        log("ERROR", f"❌ Browser automation failed at step '{result['step']}': {e}")
        return result

    except Exception as e:
        dur = time.time() - t0
        result["duration_seconds"] = dur
        result["error"] = f"Unexpected error: {type(e).__name__}: {e}"
        log("ERROR", f"❌ Unexpected error in browser automation: {e}")
        return result


