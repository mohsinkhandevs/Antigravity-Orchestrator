import threading
from typing import Callable, Optional, Dict, Any

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except Exception:
    KEYBOARD_AVAILABLE = False

try:
    from PyQt6.QtCore import QObject, pyqtSignal
    PYQT_AVAILABLE = True
except Exception:
    PYQT_AVAILABLE = False


if PYQT_AVAILABLE:
    class HotkeySignalBridge(QObject):
        """Qt signal bridge allowing hotkeys to safely dispatch to Qt main GUI thread."""
        sig_triggered = pyqtSignal(str)  # emits hotkey name (e.g. 'emergency', 'restore')
else:
    HotkeySignalBridge = None


class GlobalHotkeyManager:
    """Manages global emergency shortcuts and restore hotkeys across Windows."""

    def __init__(self):
        self._lock = threading.RLock()
        self._registered: Dict[str, Dict] = {}
        self._last_trigger_ts: Dict[str, float] = {}
        self._signal_bridge: Optional[Any] = HotkeySignalBridge() if (PYQT_AVAILABLE and HotkeySignalBridge) else None

    @property
    def signal_bridge(self) -> Optional[Any]:
        """Provides access to the Qt signal bridge for thread-safe main GUI event dispatching."""
        return self._signal_bridge

    def register(self, hotkey: str, callback: Callable, name: str = "emergency") -> bool:
        """Registers a global hotkey."""
        if not KEYBOARD_AVAILABLE or not hotkey:
            return False

        with self._lock:
            self.unregister(name)

            def _dispatch():
                try:
                    # 1. Emit through Qt signal bridge if available for queued main-thread execution
                    if self._signal_bridge:
                        try:
                            self._signal_bridge.sig_triggered.emit(name)
                        except Exception:
                            pass
                    # 2. Invoke callback on background worker thread
                    threading.Thread(target=callback, daemon=True).start()
                except Exception as e:
                    print(f"[ERROR] Exception during hotkey '{name}' execution: {e}")

            def handler():
                import time
                now_ts = time.time()
                with self._lock:
                    if now_ts - self._last_trigger_ts.get(name, 0.0) < 0.25:
                        return
                    self._last_trigger_ts[name] = now_ts
                _dispatch()

            try:
                hook = keyboard.add_hotkey(hotkey, handler)
                self._registered[name] = {
                    "hotkey": hotkey,
                    "handler": handler,
                    "dispatch": _dispatch,
                    "hook": hook,
                }
                return True
            except Exception as e:
                print(f"[WARN] Failed to register global hotkey '{hotkey}' ({name}): {e}")
                return False

    def unregister(self, name: Optional[str] = None):
        """Unregisters hotkey by name, or all hotkeys if name is None."""
        if not KEYBOARD_AVAILABLE:
            return

        with self._lock:
            if name:
                info = self._registered.pop(name, None)
                if info:
                    try:
                        keyboard.remove_hotkey(info["handler"])
                    except Exception:
                        pass
            else:
                for info in list(self._registered.values()):
                    try:
                        keyboard.remove_hotkey(info["handler"])
                    except Exception:
                        pass
                self._registered.clear()

    def is_registered(self, name: str) -> bool:
        """Checks if a hotkey with given name is currently registered."""
        with self._lock:
            return name in self._registered

    def trigger(self, name: str) -> bool:
        """Manually triggers a registered hotkey by name (useful for testing or programmatic activation)."""
        with self._lock:
            info = self._registered.get(name)
        if info:
            fn = info.get("dispatch") or info.get("handler")
            if fn:
                fn()
                return True
        return False


# Singleton instance
hotkey_manager = GlobalHotkeyManager()
