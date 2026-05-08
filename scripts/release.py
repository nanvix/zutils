#!/usr/bin/env python3
# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Cut a new zutils release.

Automates the local side of the release process:
  1. Bumps the version in pyproject.toml (patch by default).
  2. Runs validation (lint, typecheck, tests).
  3. Commits the changes with the required message format.
  4. Pushes to the dev branch.

CI then takes over: creates the tag, GitHub Release, and updates consumers.

Usage:
    python scripts/release.py              # patch bump (default)
    python scripts/release.py minor        # minor bump
    python scripts/release.py major        # major bump
    python scripts/release.py --dry-run    # preview without pushing
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# -- Helpers ------------------------------------------------------------------


def _read_version() -> str:
    """Read the current version from pyproject.toml."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def _run(
    *args: str,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command rooted at the repo directory."""
    print(f"\n> {' '.join(args)}")
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )


def _step(label: str) -> None:
    """Print a step header."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")


# -- Steps --------------------------------------------------------------------


def bump_version(bump: str) -> tuple[str, str]:
    """Bump the version and return (old, new)."""
    _step(f"Bumping version ({bump})")
    old = _read_version()
    _run("uv", "run", "tasks.py", "version", bump)
    new = _read_version()
    print(f"\n  {old} -> {new}")
    return old, new


def validate() -> None:
    """Run lint, typecheck, and tests."""
    _step("Running validation")
    _run("uv", "run", "tasks.py", "lint")
    _run("uv", "run", "tasks.py", "typecheck")
    _run("uv", "run", "tasks.py", "test")


def check_preconditions() -> None:
    """Verify that we are on dev with a clean working tree."""
    _step("Checking preconditions")

    branch = _run(
        "git", "rev-parse", "--abbrev-ref", "HEAD", capture=True
    ).stdout.strip()
    if branch != "dev":
        print(f"error: must be on 'dev' branch (currently on '{branch}')")
        sys.exit(2)

    status = _run("git", "status", "--porcelain", capture=True).stdout.strip()
    if status:
        print("error: working tree is not clean — commit or stash changes first")
        print(status)
        sys.exit(2)

    uv = shutil.which("uv")
    if uv is None:
        print("error: 'uv' not found on PATH")
        sys.exit(3)

    print("  Branch: dev ✓")
    print("  Working tree: clean ✓")
    print("  uv: found ✓")


def check_tag(version: str) -> None:
    """Ensure the tag does not already exist on the remote."""
    tag = f"v{version}"
    result = _run(
        "git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}",
        capture=True, check=False,
    )
    if result.stdout.strip():
        print(f"error: tag {tag} already exists on origin")
        sys.exit(1)
    print(f"  Tag {tag} is available ✓")


def commit_and_push(version: str) -> None:
    """Stage, commit, and push to dev."""
    _step(f"Committing and pushing v{version}")
    _run("git", "add", "-A")
    _run("git", "commit", "-m", f"[build] E: Release v{version}")
    _run("git", "push", "origin", "dev")


# -- Main --------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cut a new zutils release.",
        epilog="CI handles tag creation, GitHub Release, and consumer updates.",
    )
    parser.add_argument(
        "bump",
        nargs="?",
        default="patch",
        choices=["major", "minor", "patch"],
        help="version bump type (default: patch)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show what would happen, but don't commit or push",
    )
    args = parser.parse_args()

    try:
        check_preconditions()
        _, new_version = bump_version(args.bump)
        validate()
        check_tag(new_version)

        if args.dry_run:
            _step("Dry run — skipping commit and push")
            print(f"  Would release v{new_version}")
            print("  Resetting version bump...")
            _run("git", "checkout", "--", ".")
            return 0

        commit_and_push(new_version)

        _step("Done")
        print(f"  Released v{new_version} — pushed to dev.")
        print("  CI will now:")
        print("    • Run lint, typecheck, and tests (Linux + Windows)")
        print("    • Build wheel/sdist and template archives")
        print("    • Create tag and GitHub Release")
        print("    • Update downstream consumer repos")
        print(f"\n  Track progress: https://github.com/nanvix/zutils/actions")
        return 0

    except subprocess.CalledProcessError as exc:
        print(f"\nerror: command failed with exit code {exc.returncode}")
        print(f"  {' '.join(str(a) for a in exc.cmd)}")
        return 1
    except KeyboardInterrupt:
        print("\n\nAborted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
