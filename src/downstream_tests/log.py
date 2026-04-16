"""log.py -- Logging helpers for downstream_tests."""

from __future__ import annotations

# Module-level accumulators.  Call ``warn()`` / ``fail()`` to record messages
# and ``get_warnings()`` / ``get_errors()`` / ``print_warning_summary()`` to
# retrieve them.
_warnings: list[str] = []
_errors: list[str] = []


def log(msg: str) -> None:
    """Print an informational message with a blue bold prefix."""
    print(f"\033[1;34m>>>\033[0m {msg}")


def ok(msg: str) -> None:
    """Print a success message with a green bold prefix."""
    print(f"\033[1;32m OK\033[0m {msg}")


def fail(msg: str) -> None:
    """Print a failure message with a red bold prefix and record it."""
    print(f"\033[1;31mFAIL\033[0m {msg}")
    _errors.append(msg)


def dry(msg: str) -> None:
    """Print a dry-run notice with a yellow bold prefix."""
    print(f"\033[1;33m[dry-run]\033[0m {msg}")


def warn(msg: str) -> None:
    """Print a warning message with a yellow bold prefix and record it."""
    print(f"\033[1;33mWARN\033[0m {msg}")
    _warnings.append(msg)


def heading(msg: str) -> None:
    """Print a bold section heading with a horizontal rule."""
    rule = "=" * 64
    print()
    print(f"\033[1m{rule}\033[0m")
    print(f"\033[1m  {msg}\033[0m")
    print(f"\033[1m{rule}\033[0m")
    print()


def separator() -> None:
    """Print a thin separator line."""
    print(f"\033[2m{'-' * 64}\033[0m")


def get_warnings() -> list[str]:
    """Return a copy of the accumulated warnings."""
    return list(_warnings)


def get_errors() -> list[str]:
    """Return a copy of the accumulated errors."""
    return list(_errors)


def clear_warnings() -> None:
    """Clear the accumulated warnings."""
    _warnings.clear()


def clear_errors() -> None:
    """Clear the accumulated errors."""
    _errors.clear()


def print_warning_summary() -> None:
    """Print a summary of accumulated warnings, if any."""
    if not _warnings:
        return
    print()
    print(f"\033[1;33mWARN\033[0m {len(_warnings)} warning(s):")
    for w in _warnings:
        print(f"  \033[33m- {w}\033[0m")
