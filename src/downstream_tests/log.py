"""log.py -- Logging helpers for downstream_tests."""


def log(msg: str) -> None:
    """Print an informational message with a blue bold prefix."""
    print(f"\033[1;34m>>>\033[0m {msg}")


def ok(msg: str) -> None:
    """Print a success message with a green bold prefix."""
    print(f"\033[1;32m OK\033[0m {msg}")


def fail(msg: str) -> None:
    """Print a failure message with a red bold prefix."""
    print(f"\033[1;31mFAIL\033[0m {msg}")


def dry(msg: str) -> None:
    """Print a dry-run notice with a yellow bold prefix."""
    print(f"\033[1;33m[dry-run]\033[0m {msg}")


def warn(msg: str) -> None:
    """Print a warning message with a yellow bold prefix."""
    print(f"\033[1;33mWARN\033[0m {msg}")
