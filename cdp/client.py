import os
import time
import socket
import subprocess
import urllib.request
import json
import re
from typing import Optional, List, Tuple
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

from app.config import config


def is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    """Checks whether a TCP port is open and listening."""
    if not port or port <= 0:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def verify_cdp_port(port: int, host: str = "127.0.0.1") -> bool:
    """Verifies that the port is an active Chrome DevTools Protocol endpoint for Antigravity."""
    try:
        req = urllib.request.Request(f"http://{host}:{port}/json/version")
        with urllib.request.urlopen(req, timeout=0.35) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                return "webSocketDebuggerUrl" in data
    except Exception:
        pass
    return False


def auto_discover_antigravity_port() -> Optional[int]:
    """
    Automatically discovers the active Chrome DevTools Protocol port for Google Antigravity.
    Scans listening sockets belonging to running Antigravity processes without requiring
    the user to configure or guess dynamic ports.
    """
    # 1. Primary method: via psutil process inspection
    try:
        import psutil
        antigravity_pids = set()
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = (p.info["name"] or "").lower()
                if "antigravity" in name and "automation" not in name and "ide" not in name:
                    antigravity_pids.add(p.info["pid"])
            except Exception:
                continue

        if antigravity_pids:
            for conn in psutil.net_connections(kind="inet"):
                if conn.pid in antigravity_pids and conn.status == "LISTEN":
                    port = conn.laddr.port
                    if verify_cdp_port(port):
                        return port
    except Exception as e:
        print(f"[DEBUG] psutil port scan note: {e}")

    # 2. Fallback method: via netstat command
    try:
        out = subprocess.check_output("netstat -ano -p tcp", shell=True, text=True, stderr=subprocess.DEVNULL)
        matches = re.findall(r"127\.0\.0\.1:(\d+)\s+.*LISTENING", out)
        for port_str in matches:
            port = int(port_str)
            if verify_cdp_port(port):
                return port
    except Exception:
        pass

    return None


def find_antigravity_executable() -> Optional[str]:
    """Finds the Google Antigravity desktop executable on Windows."""
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe"),
        r"C:\Program Files\Antigravity\Antigravity.exe",
        r"C:\Program Files (x86)\Antigravity\Antigravity.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


class AntigravityCDPClient:
    """
    Manages the Chrome DevTools Protocol connection to Google Antigravity.
    Features automatic dynamic port discovery, startup bootstrapping, and page resolution.
    """

    def __init__(self, port: int = None):
        self.port = port or auto_discover_antigravity_port() or getattr(config, "cdp_port", 9222)
        self.endpoint_url = f"http://127.0.0.1:{self.port}"
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._is_connected: bool = False

    def is_connected(self) -> bool:
        if not self._is_connected or not self.page:
            return False
        try:
            return self.page.evaluate("() => true") is True
        except Exception:
            self._is_connected = False
            return False

    def launch_antigravity_with_debugging(self, timeout: float = 15.0) -> bool:
        """Launches Antigravity with the --remote-debugging-port argument."""
        exe_path = find_antigravity_executable()
        if not exe_path:
            return False

        cmd = [exe_path, f"--remote-debugging-port={self.port}"]
        try:
            subprocess.Popen(cmd, shell=False)
        except Exception as e:
            print(f"[WARN] Failed to launch Antigravity: {e}")
            return False

        # Wait for port to open
        start_ts = time.time()
        while time.time() - start_ts < timeout:
            if is_port_listening(self.port):
                return True
            time.sleep(0.5)
        return False

    def connect(self, auto_launch: bool = True, timeout: float = 10.0) -> Tuple[bool, str]:
        """
        Connects to Antigravity over CDP. Automatically resolves the active port.
        Returns (success: bool, status_message: str).
        """
        if self.is_connected():
            return True, f"Already connected to Antigravity CDP (Port {self.port})."

        # 1. Step 1: Dynamic Auto-Discovery of the active CDP port
        discovered_port = auto_discover_antigravity_port()
        if discovered_port:
            self.port = discovered_port
            self.endpoint_url = f"http://127.0.0.1:{self.port}"
            config.cdp_port = self.port
            print(f"[INFO] Auto-discovered live Antigravity CDP port: {self.port}")
        elif not is_port_listening(self.port) or not verify_cdp_port(self.port):
            # Port not found; try launching Antigravity if permitted
            if auto_launch and getattr(config, "auto_launch_antigravity", True):
                print(f"[INFO] No active CDP port found. Starting Antigravity on port {self.port}...")
                launched = self.launch_antigravity_with_debugging(timeout=timeout)
                if not launched:
                    return False, f"Could not auto-discover Antigravity CDP port or launch Antigravity with debug port."
            else:
                return False, f"Google Antigravity is not running with an active CDP endpoint. Please start Antigravity."

        try:
            if not self.playwright:
                self.playwright = sync_playwright().start()

            # Connect via CDP
            self.browser = self.playwright.chromium.connect_over_cdp(self.endpoint_url)
            self.context = self.browser.contexts[0] if self.browser.contexts else None

            if not self.context or not self.context.pages:
                return False, f"CDP connected on port {self.port}, but no active window pages found."

            # Prevent Playwright's default 'light' prefers-color-scheme emulation from turning the dark UI white
            for p in self.context.pages:
                try:
                    p.emulate_media(color_scheme="dark")
                except Exception:
                    pass

            # Find the primary workspace page
            self.page = self._resolve_primary_page(self.context.pages)
            if not self.page:
                return False, f"Connected on port {self.port}, but unable to identify main Antigravity workspace page."

            self._is_connected = True
            return True, f"Successfully connected to Antigravity on port {self.port}."

        except Exception as e:
            self._is_connected = False
            return False, f"Failed to connect to CDP on port {self.port}: {e}"

    def _resolve_primary_page(self, pages: List[Page]) -> Optional[Page]:
        """Identifies the main Antigravity workspace page among Electron targets."""
        for p in pages:
            try:
                title = p.title().lower()
                url = p.url.lower()
                if "devtools://" in url or "chrome-extension://" in url:
                    continue
                if "antigravity" in title or "antigravity" in url or "127.0.0.1" in url:
                    return p
            except Exception:
                continue

        # Fallback to first non-devtools page
        for p in pages:
            if "devtools://" not in p.url.lower():
                return p
        return pages[0] if pages else None

    def get_page(self) -> Page:
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                raise RuntimeError(msg)
        return self.page

    def disconnect(self):
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        finally:
            self.browser = None
            self.context = None
            self.page = None
            self.playwright = None
            self._is_connected = False

    def close(self):
        """Alias for disconnect() to ensure compatibility with callers expecting client.close()."""
        self.disconnect()

