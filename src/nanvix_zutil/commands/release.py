# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil release`` — package release archives from ``.nanvix/out/staging``.

Standalone command.  At package time, inspects ``.nanvix/out/staging/`` for
the two magic subdirectories ``release/`` and ``dev/``.  For each present
and non-empty subdirectory, produces one archive suffixed accordingly:

    {name}-{host}-{arch}-{machine}-{mode}-{mem}.{ext}      (release/)
    {name}-{host}-{arch}-{machine}-{mode}-{mem}-dev.{ext}  (dev/)

The archive extension is gated on host: ``linux`` -> ``.tar.gz``,
``windows`` -> ``.zip``.  If neither directory exists or both are empty,
the command fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.config import Config, Host
from nanvix_zutil.exitcodes import EXIT_GENERAL_ERROR, EXIT_SUCCESS
from nanvix_zutil.manifest import load_manifest
from nanvix_zutil.paths import dev_out, dist_dir, nanvix_root, regular_out
from nanvix_zutil.release import ArchiveFormat, package

HELP: str = "Package release archives from .nanvix/out/staging into .nanvix/out/dist"
"""One-line description surfaced in ``nanvix-zutil --help``."""

_HOST_FORMAT: dict[Host, ArchiveFormat] = {
    Host.linux: ArchiveFormat.TAR_GZ,
    Host.windows: ArchiveFormat.ZIP,
}


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``nanvix-zutil release``."""
    return argparse.ArgumentParser(
        prog="nanvix-zutil release",
        description=(
            "Package release archives from .nanvix/out/staging into"
            " .nanvix/out/dist.  Produces one archive per non-empty magic"
            " subdirectory (regular/, dev/), suffixed accordingly."
        ),
    )


def _is_non_empty_dir(path: Path) -> bool:
    """Return True when *path* exists, is a directory, and contains an entry."""
    return path.is_dir() and any(path.iterdir())


def _consumer_instance() -> object | None:
    """Instantiate the consumer's ``ZScript`` subclass if ``.nanvix/z.py`` defines one.

    Returns ``None`` when ``z.py`` is absent.  Import errors on ``z.py``
    propagate — a broken consumer script must fail loudly.
    """
    if not (nanvix_root() / "z.py").exists():
        return None
    # Lazy import: ``__main__`` imports this module at top level for HELP.
    from nanvix_zutil.__main__ import discover_script_class

    return discover_script_class()()


def consumer_required_files() -> list[Path]:
    """Return ``required_files_for_release()`` from ``.nanvix/z.py`` if defined.

    Returns ``[]`` when ``z.py`` is absent or defines no override.  The
    returned paths are archive-relative and are handed to :func:`package`
    for post-write verification.
    """
    instance = _consumer_instance()
    if instance is None:
        return []
    fn = getattr(instance, "required_files_for_release", None)
    if not callable(fn):
        return []
    result = fn()
    if not isinstance(result, list):
        return []
    return [Path(str(p)) for p in result]  # type: ignore[reportUnknownVariableType]


def release() -> None:
    """Package release archives from ``.nanvix/out/staging``."""
    manifest = load_manifest()
    config = Config()

    fmt = _HOST_FORMAT[config.host]
    base = (
        f"{manifest.name}"
        f"-{config.host}"
        f"-{config.target}"
        f"-{config.machine}"
        f"-{config.deployment_mode}"
        f"-{config.memory_size}"
    )

    require = consumer_required_files()

    # (source, archive suffix): regular/ -> no suffix, dev/ -> "-dev".
    slots: list[tuple[Path, str]] = []
    if _is_non_empty_dir(regular_out()):
        slots.append((regular_out(), ""))
    if _is_non_empty_dir(dev_out()):
        slots.append((dev_out(), "-dev"))

    if not slots:
        log.fatal(
            "Nothing to package: no non-empty regular/ or dev/ under"
            f" {regular_out().parent}.",
            code=EXIT_GENERAL_ERROR,
            hint=(
                "Stage runtime artifacts under regular_out() during build."
                " Stage headers and libraries under dev_out()."
            ),
        )

    for source, suffix in slots:
        package(
            [source], dist_dir(), f"{base}{suffix}", formats=(fmt,), require=require
        )


def main() -> None:
    """Entry point for ``nanvix-zutil release``."""
    parser = _build_parser()
    _args = parser.parse_args()
    release()
    sys.exit(EXIT_SUCCESS)
