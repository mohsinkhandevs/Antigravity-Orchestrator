"""Antigravity CDP Studio package."""
import sys

# Ensure Windows terminal standard streams handle Unicode characters and emojis safely
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if _stream and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
