import sys
import os

# Ensure Windows terminal standard streams handle Unicode characters and emojis safely
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if _stream and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

# Suppress benign Windows DPI context reassignment notice from Qt QPA
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")

def setup_crash_logging():
    def excepthook(exc_type, exc_value, exc_tb):
        import traceback
        import datetime
        err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            from app.config import get_user_data_dir
            crash_path = os.path.join(get_user_data_dir(), "crash.log")
        except Exception:
            crash_path = "crash.log"
        try:
            with open(crash_path, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.datetime.now().isoformat()}] CRASH TRACEBACK:\n{err_msg}\n")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook

setup_crash_logging()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from app.ui.main_window import MainWindow
from app.ui.floating_hud import get_mini_hud_icon
from app.config import resolve_asset_path

IPC_SERVER_NAME = "AntigravityCDP_SingleInstance_IPC"


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Antigravity CDP Studio")
    app.setOrganizationName("Antigravity")
    app.setQuitOnLastWindowClosed(False)

    # 1. Single Instance Check: Connect to existing instance if running (unless --force / -f is passed)
    force_start = "--force" in sys.argv or "-f" in sys.argv
    if not force_start:
        test_socket = QLocalSocket()
        test_socket.connectToServer(IPC_SERVER_NAME)
        if test_socket.waitForConnected(400):
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY = -1
                except Exception:
                    pass
            test_socket.write(b"RESTORE\n")
            test_socket.flush()
            if test_socket.waitForReadyRead(3000):
                ack = bytes(test_socket.readAll()).strip()
                if ack == b"ACK":
                    test_socket.disconnectFromServer()
                    print("[INFO] Antigravity CDP Studio is already running. Restored existing studio window to front.")
                    sys.exit(0)
            # Server didn't respond or is stale/deadlocked: clean up stale socket and launch fresh
            test_socket.disconnectFromServer()
            QLocalServer.removeServer(IPC_SERVER_NAME)
    else:
        QLocalServer.removeServer(IPC_SERVER_NAME)

    # 2. Start IPC Local Server for single-instance management
    ipc_server = QLocalServer()
    QLocalServer.removeServer(IPC_SERVER_NAME)
    if not ipc_server.listen(IPC_SERVER_NAME):
        print(f"[WARN] Unable to listen on IPC server: {ipc_server.errorString()}")

    # Set application icon to custom HUD Mini SVG Logo
    app_icon = get_mini_hud_icon(64)
    if app_icon.isNull():
        icon_path = resolve_asset_path("project_plus_btn.png")
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    window = MainWindow(app_icon=app_icon)
    window.setWindowTitle("Antigravity CDP Studio")
    window.show()

    # IPC connection handler
    def handle_new_ipc_connection():
        client = ipc_server.nextPendingConnection()
        if client:
            try:
                client.waitForReadyRead(200)
                client.write(b"ACK\n")
                client.flush()
                client.waitForBytesWritten(200)
                client.disconnectFromServer()
            except Exception:
                pass
        window.restore_and_bring_to_front()

    ipc_server.newConnection.connect(handle_new_ipc_connection)

    exit_code = app.exec()
    try:
        ipc_server.close()
        QLocalServer.removeServer(IPC_SERVER_NAME)
    except Exception:
        pass
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
