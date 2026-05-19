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
    "lint",
    "format",
    "install",
    "help",
)

#: Subcommands that accept the ``--with-docker`` flag.
#:
#: ``--with-docker IMAGE`` is required on ``setup`` to specify the Docker
#: image.  ``build``, ``release``, and ``clean`` load the image from
#: persisted config (set during ``setup``).
DOCKER_SUBCOMMANDS: tuple[str, ...] = ("setup",)

#: Human-readable descriptions for each subcommand.
SUBCOMMAND_HELP: dict[str, str] = {
    "setup": "Prepare the build environment",
    "distclean": "Remove all transient .nanvix/ artifacts",
    "build": "Build the project",
    "test": "Run tests",
    "benchmark": "Run benchmarks",
    "release": "Package a release",
    "clean": "Remove build artifacts",
    "lock": "Resolve dependencies and write nanvix.lock",
    "lint": "Run linters (black --check + pyright) on .nanvix/*.py",
    "format": "Format .nanvix/*.py with black",
    "install": "Export build artifacts to a target directory",
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
        sub = subparsers.add_parser(name, help=SUBCOMMAND_HELP[name])
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
        if name == "format":
            sub.add_argument(
                "--check",
                action="store_true",
                default=False,
                help="Check formatting without modifying files (exit non-zero on diff)",
            )
        if name in DOCKER_SUBCOMMANDS:
            sub.add_argument(
                "--with-docker",
                type=str,
                required=True,
                metavar="IMAGE",
                dest="with_docker",
                help="Docker image to use for containerised builds."
                " The image is persisted to .nanvix/env.json so that"
                " subsequent build/release/clean commands use it"
                " automatically. test and benchmark always run on"
                " the host.",
            )
            sub.add_argument(
                "--offline",
                action="store_true",
                default=False,
                help="Skip the dependency resolver entirely and require"
                " all artifacts to be available locally via --with-nanvix.",
            )
            sub.add_argument(
                "--with-nanvix",
                type=str,
                metavar="PATH",
                dest="with_nanvix",
                help="Path to a local build directory containing"
                " deps/<name>/{lib,include}/ artifacts."
                " Overrides the WITH_NANVIX environment variable.",
            )
            sub.add_argument(
                "--sysroot-path",
                type=str,
                metavar="PATH",
                dest="sysroot_path",
                help="Explicit path to a local sysroot directory."
                " Alternative to setting NANVIX_VERSION to a path.",
            )
        if name == "install":
            sub.add_argument(
                "--output",
                type=str,
                required=True,
                metavar="PATH",
                dest="output",
                help="Target directory to export build artifacts to."
                " Creates <output>/{lib,include,bin}/ subdirectories.",
            )

    return parser
