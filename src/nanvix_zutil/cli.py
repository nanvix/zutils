# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Argument parsing and subcommand dispatch for nanvix_zutil consumers.

This module is an internal implementation detail of :class:`~nanvix_zutil.ZScript`.
Consumer scripts do not construct the parser directly; they call
``ZScript.main()`` which delegates here.
"""

from __future__ import annotations

import argparse

from nanvix_zutil.lockfile import get_zutil_version

# ---------------------------------------------------------------------------
# Version helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Parser factory
# ---------------------------------------------------------------------------

#: All lifecycle subcommand names.
SUBCOMMANDS: tuple[str, ...] = (
    "setup",
    "build",
    "test",
    "benchmark",
    "release",
    "clean",
    "lock",
    "help",
)

#: Human-readable descriptions for each subcommand.
_SUBCOMMAND_HELP: dict[str, str] = {
    "setup": "Prepare the build environment",
    "build": "Build the project",
    "test": "Run tests",
    "benchmark": "Run benchmarks",
    "release": "Package a release",
    "clean": "Remove build artifacts",
    "lock": "Resolve dependencies and write nanvix.lock",
    "help": "Show help message",
}


def build_parser(prog: str = "./z") -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Args:
        prog: Program name shown in ``--help`` output.

    Returns:
        Fully configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Nanvix build script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-parseable JSON output",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s (nanvix-zutil {get_zutil_version()})",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    for name in SUBCOMMANDS:
        sub = subparsers.add_parser(name, help=_SUBCOMMAND_HELP[name])
        if name == "lock":
            lock_group = sub.add_mutually_exclusive_group()
            lock_group.add_argument(
                "--check",
                action="store_true",
                default=False,
                help="Verify that nanvix.lock is up-to-date (exit 3 if missing, 2 if stale)",
            )
            lock_group.add_argument(
                "--shallow",
                action="store_true",
                default=False,
                help="Resolve only direct dependencies (skip transitive discovery)",
            )

    return parser
