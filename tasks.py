# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""
Development task runner for nanvix-zutil.

Usage: uv run tasks.py <command>

Commands:
  setup      Configure git hooks and sync dev dependencies
  lint       Check code formatting with black
  format     Fix code formatting with black
  typecheck  Run strict type checking with basedpyright
  test       Run the test suite with pytest
  clean      Remove Python bytecode caches and build artifacts
"""

import shutil
import subprocess
import sys
from pathlib import Path
from collections.abc import Callable

SOURCES = ["src/", "tests/"]


def _run(*args: str) -> int:
    """Run a command, returning its exit code."""
    print(f"> {' '.join(args)}")
    return subprocess.call(args)


def setup() -> int:
    """Configure git hooks and sync dev dependencies."""
    return _run("git", "config", "--local", "core.hooksPath", ".githooks")


def lint() -> int:
    """Check code formatting with black."""
    return _run(sys.executable, "-m", "black", "--check", *SOURCES)


def format_code() -> int:
    """Fix code formatting with black."""
    return _run(sys.executable, "-m", "black", *SOURCES)


def typecheck() -> int:
    """Run strict type checking with basedpyright."""
    return _run(sys.executable, "-m", "basedpyright", *SOURCES)


def test() -> int:
    """Run the test suite with pytest."""
    return _run(sys.executable, "-m", "pytest", "tests/", "-v")


def clean() -> int:
    """Remove Python bytecode caches and build artifacts."""
    count = 0
    for pattern, is_dir in [("__pycache__", True), ("*.pyc", False)]:
        for p in Path(".").rglob(pattern):
            if is_dir:
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
            count += 1
    for d in [".pytest_cache", "dist", "build"]:
        path = Path(d)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            count += 1
    print(f"Cleaned {count} item(s).")
    return 0


COMMANDS: dict[str, tuple[Callable[[], int], str]] = {
    "setup": (setup, "Configure git hooks and sync dev dependencies"),
    "lint": (lint, "Check code formatting with black"),
    "format": (format_code, "Fix code formatting with black"),
    "typecheck": (typecheck, "Run strict type checking with basedpyright"),
    "test": (test, "Run the test suite with pytest"),
    "clean": (clean, "Remove Python bytecode caches and build artifacts"),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(2)

    fn, _ = COMMANDS[cmd]
    sys.exit(fn())


if __name__ == "__main__":
    main()
