# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil distclean`` — remove all transient ``.nanvix/`` artifacts."""

from __future__ import annotations

import argparse
import shutil
import sys

from nanvix_zutil import log
from nanvix_zutil.constants import NANVIX_ROOT
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
            "Remove all transient .nanvix/ artifacts."
            "Removal is best-effort: artifacts "
            "that cannot be deleted (e.g. a locked venv on Windows) are "
            "skipped with a warning.\n\n"
            "If a .nanvix/z.py exists in the consumer repo, its ZScript "
            "subclass is imported and its clean() hook is invoked before "
            "artifact removal.  Any failure in that step is logged as a "
            "warning and does not abort the distclean.\n\n"
            "Aside from those defined in `clean()`, the following artifacts are removed:\n"
            + ",\n".join(_ARTIFACTS)
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return parser


def distclean() -> None:
    for artifact in _ARTIFACTS:
        path = NANVIX_ROOT / artifact
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


def _run_consumer_clean() -> None:
    z_py = NANVIX_ROOT / "z.py"
    if not z_py.exists():
        return

    # Lazy import: ``__main__`` imports this module at top level for HELP.
    from nanvix_zutil.__main__ import discover_script_class

    try:
        script_cls = discover_script_class(z_py)
        instance = script_cls()
        instance.clean()
    except (Exception, SystemExit) as exc:
        log.warning(f"Consumer clean() failed: {exc}; continuing with distclean")


def main() -> None:
    """Entry point for ``nanvix-zutil distclean``."""
    parser = _build_parser()
    _args = parser.parse_args()

    _run_consumer_clean()
    distclean()

    sys.exit(EXIT_SUCCESS)
