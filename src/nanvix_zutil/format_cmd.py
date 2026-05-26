# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil format`` — format ``.nanvix/*.py`` with black.

Standalone command (per issue #189): does not require a ``ZScript``
subclass.  Runs ``black`` on all Python files in the target ``.nanvix/``
directory, using the ``black.toml`` configuration alongside them.
With ``--check``, runs ``black --check`` instead (exits non-zero on diff).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_SUCCESS
from nanvix_zutil.helpers import ensure_tool_installed, run

HELP: str = "Format <nanvix-dir>/*.py with black"
"""One-line description surfaced in ``nanvix-zutil --help``."""


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``nanvix-zutil format``.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="nanvix-zutil format",
        description=(
            "Format every *.py file in the nanvix directory with black,"
            " using the black configuration file shipped alongside them."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Check formatting without modifying files (exit non-zero on diff)",
    )
    return parser


def format(check: bool = False) -> None:
    nanvix_dir = Path(sys.prefix).parent

    py_files = sorted(nanvix_dir.glob("*.py"))
    if not py_files:
        log.warning(f"No .py files found in {nanvix_dir} — nothing to format")
        return
    ensure_tool_installed("black")
    str_files = [str(f) for f in py_files]
    black_cfg = str(nanvix_dir / "black.toml")
    cmd = [sys.executable, "-m", "black", "--config", black_cfg]
    if check:
        cmd.append("--check")
    cmd.extend(str_files)
    run(*cmd)


def main() -> None:
    """Entry point for ``nanvix-zutil format``."""
    parser = _build_parser()
    args = parser.parse_args()

    format(check=args.check)

    sys.exit(EXIT_SUCCESS)
