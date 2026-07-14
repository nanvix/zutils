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
from nanvix_zutil.paths import dev_out, dist_dir, release_out
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
            " subdirectory (release/, dev/), suffixed accordingly."
        ),
    )


def _is_non_empty_dir(path: Path) -> bool:
    """Return True when *path* exists, is a directory, and contains an entry."""
    return path.is_dir() and any(path.iterdir())


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

    # (source, archive suffix): release/ -> no suffix, dev/ -> "-dev".
    slots: list[tuple[Path, str]] = []
    if _is_non_empty_dir(release_out()):
        slots.append((release_out(), ""))
    if _is_non_empty_dir(dev_out()):
        slots.append((dev_out(), "-dev"))

    if not slots:
        log.fatal(
            "Nothing to package: no non-empty release/ or dev/ under"
            f" {release_out().parent}.",
            code=EXIT_GENERAL_ERROR,
            hint=(
                "Stage runtime artifacts under release_out() during build."
                " Stage headers and libraries under dev_out()."
            ),
        )

    for source, suffix in slots:
        package([source], dist_dir(), f"{base}{suffix}", formats=(fmt,))


def main() -> None:
    """Entry point for ``nanvix-zutil release``."""
    parser = _build_parser()
    _args = parser.parse_args()
    release()
    sys.exit(EXIT_SUCCESS)
