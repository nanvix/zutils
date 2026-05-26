# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil distclean`` — remove all transient ``.nanvix/`` artifacts."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_SUCCESS

HELP: str = "Remove all transient .nanvix/ artifacts"
"""One-line description surfaced in ``nanvix-zutil --help``."""

#: Transient artifacts removed by ``distclean``.  Only the manifest
#: (``nanvix.toml``) and lockfile (``nanvix.lock``) are preserved.
_ARTIFACTS: tuple[str, ...] = (
    "sysroot",
    "buildroot",
    "cache",
    "env.json",
    "venv",
    "__pycache__",
)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``nanvix-zutil distclean``.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="nanvix-zutil distclean",
        description=(
            "Remove all transient .nanvix/ artifacts: sysroot, buildroot, "
            "cache, venv, __pycache__, and env.json.  Only nanvix.toml and "
            "nanvix.lock are preserved.  Removal is best-effort: artifacts "
            "that cannot be deleted (e.g. a locked venv on Windows) are "
            "skipped with a warning.\n\n"
            "If a .nanvix/z.py exists in the consumer repo, its ZScript "
            "subclass is imported and its clean() hook is invoked before "
            "artifact removal.  Any failure in that step is logged as a "
            "warning and does not abort the distclean."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--nanvix-dir",
        default=".nanvix",
        metavar="PATH",
        dest="nanvix_dir",
        help="Path to the .nanvix/ directory (default: .nanvix)",
    )
    return parser


def distclean(nanvix_dir: Path) -> None:
    """Remove all transient ``.nanvix/`` artifacts.

    Deletes the ``sysroot``, ``buildroot``, ``cache``, ``venv``, and
    ``__pycache__`` directories and the ``env.json`` config file inside
    ``nanvix_dir``.  Only the manifest (``nanvix.toml``) and lockfile
    (``nanvix.lock``) are preserved.

    Removal is best-effort: artifacts that cannot be deleted (e.g. a
    locked venv on Windows) are skipped with a warning so the remaining
    artifacts are still cleaned.

    Args:
        nanvix_dir: Path to the ``.nanvix/`` directory.
    """
    for artifact in _ARTIFACTS:
        path = nanvix_dir / artifact
        if not path.exists() and not path.is_symlink():
            continue
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                continue
            log.info(f"Removed {path}")
        except OSError as exc:
            log.warning(f"Could not remove {path}: {exc}")


def _run_consumer_clean(nanvix_dir: Path) -> None:
    """Invoke the consumer's ``ZScript.clean()`` if a ``z.py`` exists.

    Locates ``<nanvix_dir>/z.py``, imports it via
    :func:`nanvix_zutil.__main__.discover_script_class`, instantiates the
    discovered :class:`~nanvix_zutil.script.ZScript` subclass, and calls
    ``clean()`` on it.  This mirrors what ``./z clean`` would execute,
    just driven from the standalone command.

    Best-effort: any failure (missing manifest, import error, missing
    subclass, ``clean()`` raising, or the underlying discovery helper
    calling ``log.fatal``) is logged as a warning and silently swallowed
    so the surrounding artifact removal still runs.

    Args:
        nanvix_dir: Path to the ``.nanvix/`` directory.
    """
    z_py = nanvix_dir / "z.py"
    if not z_py.exists():
        return

    repo_root = nanvix_dir.parent.resolve()

    # Lazy import: ``__main__`` imports this module at top level for HELP.
    from nanvix_zutil.__main__ import discover_script_class

    try:
        script_cls = discover_script_class(z_py)
        instance = script_cls(repo_root)
        instance.clean()
    except (Exception, SystemExit) as exc:
        log.warning(f"Consumer clean() failed: {exc}; continuing with distclean")


def main() -> None:
    """Entry point for ``nanvix-zutil distclean``."""
    parser = _build_parser()
    args = parser.parse_args()

    nanvix_dir = Path(args.nanvix_dir)
    _run_consumer_clean(nanvix_dir)
    distclean(nanvix_dir)

    sys.exit(EXIT_SUCCESS)
