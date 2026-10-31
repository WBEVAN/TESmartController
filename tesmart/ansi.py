"""ANSI color helpers for interactive terminals.

``NO_COLOR`` or a non-TTY stdout leaves text plain. Windows 10+ consoles need
virtual-terminal mode enabled once.
"""

from __future__ import annotations

import os
import sys

STYLES = {
    "title": "1;36",
    "heading": "1;36",
    "ok": "1;32",
    "warn": "33",
    "error": "1;31",
    "dim": "2",
    "command": "1",
    "url": "4",
    "proxy": "1;32",
    "client": "1;34",
    "mesh": "1;35",
    "standalone": "1;37",
}


def ansi_supported(stream: object | None = None) -> bool:
    """True when ``stream`` can show ANSI color on Windows, macOS, or Linux."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR", "").strip() not in {"", "0", "false", "no"}:
        return True
    if os.environ.get("TERM", "") == "dumb":
        return False
    target = sys.stdout if stream is None else stream
    if not hasattr(target, "isatty") or not target.isatty():  # type: ignore[union-attr]
        return False
    if sys.platform == "win32":
        return _enable_windows_vt(target)
    return True


def _enable_windows_vt(stream: object) -> bool:
    """Turn on ANSI processing for a Windows console. False on older hosts."""
    try:
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle_id = -12 if stream is sys.stderr else -11
        handle = kernel.GetStdHandle(handle_id)
        mode = ctypes.c_uint()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False
        enable = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return kernel.SetConsoleMode(handle, mode.value | enable) != 0
    except (AttributeError, OSError):
        return False


def paint(text: str, style: str | None, *, enabled: bool) -> str:
    """Wrap ``text`` in an ANSI style, or return it unchanged."""
    if not enabled or not style or not text:
        return text
    code = STYLES.get(style)
    if not code:
        return text
    return f"\033[{code}m{text}\033[0m"
