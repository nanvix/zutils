# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Argument parsing and subcommand dispatch for nanvix_zutil consumers.

This module is an internal implementation detail of :class:`~nanvix_zutil.ZScript`.
Consumer scripts do not construct the parser directly; they call
``ZScript.main()`` which delegates here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nanvix_zutil.lockfile import get_zutil_version
from nanvix_zutil.docker import DEFAULT_DOCKER_IMAGE

# ---------------------------------------------------------------------------
# Version helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Parser factory
# ---------------------------------------------------------------------------

#: All lifecycle subcommand names.
SUBCOMMANDS: tuple[str, ...] = (
    "setup",
    "distclean",
    "build",
    "test",
    "benchmark",
    "release",
    "clean",
    "lock",
    "help",
)

#: Subcommands that accept Docker flags.
DOCKER_SUBCOMMANDS: tuple[str, ...] = ("build", "release")

#: Human-readable descriptions for each subcommand.
_SUBCOMMAND_HELP: dict[str, str] = {
    "setup": "Prepare the build environment",
    "distclean": "Remove all transient .nanvix/ artifacts",
    "build": "Build the project",
    "test": "Run tests",
    "benchmark": "Run benchmarks",
    "release": "Package a release",
    "clean": "Remove build artifacts",
    "lock": "Resolve dependencies and write nanvix.lock",
    "help": "Show help message",
}


def build_parser(
    prog: str | None = None,
    available: tuple[str, ...] | None = None,
) -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Args:
        prog: Program name shown in ``--help`` output.  When ``None``
            (the default), derived from ``sys.argv[0]``.
        available: Subset of :data:`SUBCOMMANDS` to register.  When
            ``None`` all subcommands are registered (backwards-compatible
            default used by tests and the minimal pre-parser).

    Returns:
        Fully configured :class:`argparse.ArgumentParser`.
    """
    if prog is None:
        prog = Path(sys.argv[0]).stem
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

    parser.add_argument(
        "--all-builds",
        action="store_true",
        default=False,
        dest="all_builds",
        help="Expand the [builds] matrix and run the hook for every combo in parallel",
    )
    parser.add_argument(
        "--mode",
        metavar="MODE",
        default=None,
        dest="mode",
        help="Filter the build matrix to only combos matching this"
        " deployment mode (also settable via NANVIX_DEPLOYMENT_MODE)",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    if available is None:
        cmds: tuple[str, ...] = SUBCOMMANDS
    else:
        # Validate that all requested subcommands are known.
        unknown = sorted({name for name in available if name not in SUBCOMMANDS})
        if unknown:
            valid = ", ".join(SUBCOMMANDS)
            bad = ", ".join(unknown)
            msg = (
                "Unknown subcommand(s) in 'available': "
                f"{bad}. Valid subcommands are: {valid}."
            )
            raise ValueError(msg)

        # De-duplicate while preserving the original order provided in ``available``.
        seen: set[str] = set()
        deduped: list[str] = []
        for name in available:
            if name not in seen:
                seen.add(name)
                deduped.append(name)
        cmds = tuple(deduped)

    for name in cmds:
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
        if name in DOCKER_SUBCOMMANDS:
            docker_group = sub.add_mutually_exclusive_group()
            docker_group.add_argument(
                "--with-docker",
                action="store_true",
                default=False,
                dest="with_docker",
                help="Run commands inside the project's default Docker image"
                f" (default: {DEFAULT_DOCKER_IMAGE})",
            )
            docker_group.add_argument(
                "--with-minimal-docker",
                action="store_true",
                default=False,
                dest="with_minimal_docker",
                help=f"Run commands inside {DEFAULT_DOCKER_IMAGE}",
            )
            docker_group.add_argument(
                "--docker-image",
                metavar="IMAGE",
                default=None,
                dest="docker_image",
                help="Run commands inside the specified Docker image",
            )

    return parser
