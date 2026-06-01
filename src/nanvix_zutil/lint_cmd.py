# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil lint`` — run linters on ``.nanvix/*.py``.

Standalone command (per issue #189): does not require a ``ZScript``
subclass.  Runs ``black --check`` followed by ``pyright`` on all Python
files in the target ``.nanvix/`` directory, using the configurations
shipped alongside them (``black.toml`` and ``pyrightconfig.json``).
"""

from __future__ import annotations

import argparse
import sys

from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_SUCCESS
from nanvix_zutil.helpers import ensure_tool_installed, run
from nanvix_zutil.paths import nanvix_root

HELP: str = "Run linters (black --check + pyright) on <nanvix-dir>/*.py"
"""One-line description surfaced in ``nanvix-zutil --help``."""


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``nanvix-zutil lint``.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="nanvix-zutil lint",
        description=(
            "Run black --check and pyright on every *.py file in the"
            " nanvix directory, using the black and pyright configuration"
            " files shipped alongside them."
        ),
    )
    return parser


def lint() -> None:
    # venv root is always bootstrapped at .nanvix/venv
    py_files = sorted(nanvix_root().glob("*.py"))
    if not py_files:
        log.warning(f"No .py files found in {nanvix_root()} — nothing to lint")
        return
    for tool in ("black", "pyright"):
        ensure_tool_installed(tool)

    str_files = [str(f) for f in py_files]
    black_cfg = str(nanvix_root() / "black.toml")
    pyright_cfg = str(nanvix_root() / "pyrightconfig.json")
    run(
        sys.executable,
        "-m",
        "black",
        "--config",
        black_cfg,
        "--check",
        *str_files,
    )
    run(
        sys.executable,
        "-m",
        "pyright",
        "--project",
        pyright_cfg,
        *str_files,
    )


def main() -> None:
    """Entry point for ``nanvix-zutil lint``."""
    parser = _build_parser()
    _args = parser.parse_args()
    lint()

    sys.exit(EXIT_SUCCESS)
