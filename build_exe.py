"""
PyInstaller Standalone Executable Build Script
==============================================
Packages the entire Antigravity CDP Studio and integrated Browser OCR Automation
into a unified standalone distribution.

Usage:
    python build_exe.py [--onefile]
"""

import sys
import subprocess
import shutil
import os


def build_executable():
    print("=" * 60)
    print("  Antigravity CDP Studio — Executable Builder")
    print("=" * 60)

    # Default to single file .exe unless --onedir is explicitly passed
    is_onedir = "--onedir" in sys.argv
    mode_flag = "--onedir" if is_onedir else "--onefile"
    print(f"[*] Packaging Mode: {'Directory Bundle' if is_onedir else 'Standalone Single Executable (.exe)'}")

    # Ensure output directories exist
    os.makedirs("dist", exist_ok=True)
    os.makedirs("build", exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        mode_flag,
        "--noconfirm",
        "--clean",
        "--name",
        "Antigravity_CDP_Studio",
        "--windowed",
        "--icon",
        "assets/app_icon.ico",
        "--add-data",
        "assets;assets",
        "--collect-all",
        "playwright",
        "--collect-all",
        "rapidocr_onnxruntime",
        "--hidden-import",
        "keyboard",
        "--hidden-import",
        "psutil",
        "--hidden-import",
        "pyperclip",
        "--hidden-import",
        "cdp",
        "--collect-submodules",
        "cdp",
        "--hidden-import",
        "app",
        "--collect-submodules",
        "app",
        "--hidden-import",
        "PyQt6",
        "--hidden-import",
        "pyautogui",
        "--hidden-import",
        "pygetwindow",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "numpy",
        "main.py",
    ]

    print("[*] Running command:")
    print("   ", " ".join(cmd))
    print("-" * 60)

    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("=" * 60)
        print("  BUILD SUCCESSFUL!")
        target_path = os.path.abspath(
            os.path.join("dist", "Antigravity_CDP_Studio" if is_onedir else "Antigravity_CDP_Studio.exe")
        )
        print(f"  Output location: {target_path}")
        print("=" * 60)
    else:
        print(f"[!] Build failed with exit code {res.returncode}")
        sys.exit(res.returncode)


if __name__ == "__main__":
    build_executable()
