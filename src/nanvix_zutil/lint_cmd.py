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
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS, EXIT_SUCCESS
from nanvix_zutil.helpers import ensure_tool_installed, run

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
            "Run black --check and pyright on .nanvix/*.py using the"
            " black.toml and pyrightconfig.json files in the same"
            " directory."
        ),
    )
    return parser


def lint() -> None:
    """Run ``black --check`` and ``pyright`` on every ``.py`` file in
    *nanvix_dir*.

    Args:
        nanvix_dir: Directory containing the Python files to lint and the
            black/pyright configuration files.

    Behaviour mirrors the previous ``ZScript.lint`` implementation: warns
    and returns when no ``.py`` files are present, exits with
    :data:`EXIT_MISSING_DEP` if either tool is missing, and exits with
    :data:`EXIT_BUILD_FAILURE` if either tool reports problems.  Exits
    with :data:`EXIT_INVALID_ARGS` if *nanvix_dir* is not an existing
    directory.
    """
    # venv root is always bootstrapped at .nanvix/venv
    nanvix_dir = Path(sys.prefix).parent
    if not nanvix_dir.is_dir():
        log.fatal(
            f"Not a directory: {nanvix_dir}",
            code=EXIT_INVALID_ARGS,
            hint="Pass --nanvix-dir PATH pointing at an existing directory.",
        )
    py_files = sorted(nanvix_dir.glob("*.py"))
    if not py_files:
        log.warning(f"No .py files found in {nanvix_dir} — nothing to lint")
        return
    for tool in ("black", "pyright"):
        ensure_tool_installed(tool)

    str_files = [str(f) for f in py_files]
    black_cfg = str(nanvix_dir / "black.toml")
    pyright_cfg = str(nanvix_dir / "pyrightconfig.json")
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
