# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Structured logging for nanvix_zutil.

Provides colored terminal output and ``--json`` mode. In JSON mode every
message is emitted as a single-line JSON object with ``level``, ``code``,
``message``, and an optional ``hint`` field.
"""

from __future__ import annotations

import json
import os
import sys
from typing import NoReturn

# ---------------------------------------------------------------------------
# Windows ANSI support
# ---------------------------------------------------------------------------


def _enable_ansi_on_windows() -> None:
    """Enable ANSI escape sequences on Windows terminals.

    On Windows 10 1607+ this activates Virtual Terminal Processing.
    On older Windows versions this is a silent no-op — ANSI codes will
    render as raw text but the program will not crash.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        get_std_handle = kernel32.GetStdHandle
        get_std_handle.argtypes = [wintypes.DWORD]
        get_std_handle.restype = wintypes.HANDLE

        get_console_mode = kernel32.GetConsoleMode
        get_console_mode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_console_mode.restype = wintypes.BOOL

        set_console_mode = kernel32.SetConsoleMode
        set_console_mode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        set_console_mode.restype = wintypes.BOOL

        invalid_handle = ctypes.c_void_p(-1).value
        enable_vt_processing = 0x0004

        # STD_ERROR_HANDLE = -12
        handle = get_std_handle(wintypes.DWORD(-12))
        if handle in (None, 0, invalid_handle):
            return

        mode = wintypes.DWORD()
        if not get_console_mode(handle, ctypes.byref(mode)):
            return

        set_console_mode(handle, mode.value | enable_vt_processing)
    except (AttributeError, OSError, ValueError):
        pass


_enable_ansi_on_windows()

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_json_mode: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_json_mode(enabled: bool) -> None:
    """Enable or disable JSON output mode.

    When enabled every log call emits a single-line JSON object instead of
    colored plain text.

    Args:
        enabled: ``True`` to enable JSON mode, ``False`` to disable it.
    """
    global _json_mode
    _json_mode = enabled


def is_json_mode() -> bool:
    """Return whether JSON output mode is currently enabled.

    Returns:
        ``True`` if JSON mode is active, ``False`` otherwise.
    """
    return _json_mode


def info(msg: str) -> None:
    """Emit an informational message.

    Args:
        msg: The message text.
    """
    if _json_mode:
        print(
            json.dumps({"level": "info", "message": msg}),
            file=sys.stderr,
            flush=True,
        )
    else:
        print(f"\033[36minfo:\033[0m {msg}", file=sys.stderr, flush=True)


def success(msg: str) -> None:
    """Emit a success message.

    Args:
        msg: The message text.
    """
    if _json_mode:
        print(
            json.dumps({"level": "success", "message": msg}),
            file=sys.stderr,
            flush=True,
        )
    else:
        print(f"\033[32msuccess:\033[0m {msg}", file=sys.stderr, flush=True)


def warning(msg: str, code: int | None = None) -> None:
    """Emit a warning message.

    Args:
        msg: The message text.
        code: Optional process exit code associated with the warning.
    """
    if _json_mode:
        obj: dict[str, object] = {"level": "warning", "message": msg}
        if code is not None:
            obj["code"] = code
        print(json.dumps(obj), file=sys.stderr, flush=True)
    else:
        print(f"\033[33mwarning:\033[0m {msg}", file=sys.stderr, flush=True)


def error(msg: str, hint: str | None = None) -> None:
    """Emit an error message without exiting.

    Args:
        msg: The error message text.
        hint: Optional hint to help the user recover.
    """
    if _json_mode:
        obj: dict[str, object] = {"level": "error", "message": msg}
        if hint is not None:
            obj["hint"] = hint
        print(json.dumps(obj), file=sys.stderr, flush=True)
    else:
        print(f"\033[31merror:\033[0m {msg}", file=sys.stderr, flush=True)
        if hint is not None:
            print(f"\033[90mhint:\033[0m {hint}", file=sys.stderr, flush=True)


def note(msg: str) -> None:
    """Emit a contextual note (supplementary information after an error).

    Args:
        msg: The note text.
    """
    if _json_mode:
        print(
            json.dumps({"level": "note", "message": msg}),
            file=sys.stderr,
            flush=True,
        )
    else:
        print(f"\033[90mnote:\033[0m {msg}", file=sys.stderr, flush=True)


def fatal(msg: str, code: int = 1, hint: str | None = None) -> NoReturn:
    """Emit an error message and exit with the given exit code.

    Args:
        msg: The error message text.
        code: The process exit code (default ``1``).
        hint: Optional hint to help the user recover.
    """
    if _json_mode:
        obj: dict[str, object] = {"level": "error", "code": code, "message": msg}
        if hint is not None:
            obj["hint"] = hint
        print(json.dumps(obj), file=sys.stderr, flush=True)
    else:
        print(f"\033[31merror:\033[0m {msg}", file=sys.stderr, flush=True)
        if hint is not None:
            print(f"\033[90mhint:\033[0m {hint}", file=sys.stderr, flush=True)
    sys.exit(code)
