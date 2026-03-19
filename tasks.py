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
  ci         Run CI locally using gh act (requires Docker + nanvix toolchain image)
  clean      Remove Python bytecode caches and build artifacts
  release    Build distribution artifacts (wheel + sdist) for release
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


def ci() -> int:
    """Run CI locally using gh act (requires Docker + nanvix toolchain image).

    Runs the specified CI job (or all jobs) locally using nektos/act via the
    gh CLI extension. The nanvix/toolchain:latest-minimal Docker image must
    be available locally.

    Usage:
        uv run tasks.py ci            # run all CI jobs
        uv run tasks.py ci test       # run only the test job
        uv run tasks.py ci lint       # run only the lint job
    """
    if shutil.which("gh") is None:
        print("error: gh CLI not found. Install from https://cli.github.com/")
        return 1

    result = subprocess.run(
        ["gh", "act", "--help"],
        capture_output=True,
    )
    if result.returncode != 0:
        print("error: gh act extension not found.")
        print("  Install with: gh extension install nektos/gh-act")
        return 1

    target = sys.argv[2] if len(sys.argv) > 2 else "all"

    job_map: dict[str, list[str]] = {
        "lint": ["lint-and-typecheck"],
        "test": ["test"],
        "all": ["lint-and-typecheck", "test"],
    }

    jobs = job_map.get(target)
    if jobs is None:
        print(f"error: Unknown CI target: {target}")
        print(f"  Available: {', '.join(job_map)}")
        return 2

    act_flags = ["--container-architecture", "linux/amd64", "--pull=false"]

    for job in jobs:
        print(f"ci: Running job: {job}")
        rc = _run("gh", "act", "-j", job, *act_flags)
        if rc != 0:
            print(f"ci: Job '{job}' failed.")
            return rc

    print("ci: All jobs passed.")
    return 0

def release() -> int:
    """Build distribution artifacts (wheel and sdist) for release.

    Produces a wheel (.whl) and source distribution (.tar.gz) inside
    dist/.  To publish, trigger the 'Release' GitHub Actions workflow
    (workflow_dispatch) which builds, tags, creates a GitHub release,
    and publishes to PyPI via ``uv publish``.
    """
    uv = shutil.which("uv")
    if uv is None:
        print("error: 'uv' not found on PATH. Install it from https://astral.sh/uv")
        return 1
    code = _run(uv, "build")
    if code != 0:
        return code
    dist = Path("dist")
    artifacts = sorted(dist.glob("*"))
    if artifacts:
        print("Built artifacts:")
        for f in artifacts:
            print(f"  {f}")
    return 0


COMMANDS: dict[str, tuple[Callable[[], int], str]] = {
    "setup": (setup, "Configure git hooks and sync dev dependencies"),
    "lint": (lint, "Check code formatting with black"),
    "format": (format_code, "Fix code formatting with black"),
    "typecheck": (typecheck, "Run strict type checking with basedpyright"),
    "test": (test, "Run the test suite with pytest"),
    "ci": (ci, "Run CI locally using gh act (requires Docker + nanvix toolchain image)"),
    "clean": (clean, "Remove Python bytecode caches and build artifacts"),
    "release": (release, "Build distribution artifacts (wheel and sdist)"),
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
