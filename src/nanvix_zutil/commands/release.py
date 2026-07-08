# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil release`` — package release archives from ``.nanvix/out/release``.

Standalone command (per issue #191): does not require a ``ZScript``
subclass, but honours a consumer-defined ``release_targets()`` override
on ``.nanvix/z.py`` when present.

Archives are written to ``.nanvix/out/dist`` under the manifest package
name.  With no override, a single archive is produced covering the
entire release directory.  With an override, one archive per entry is
produced from the named subdirectory.
"""

from __future__ import annotations

import argparse
import re
import sys

from nanvix_zutil import log
from nanvix_zutil.config import Config
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS, EXIT_SUCCESS
from nanvix_zutil.manifest import load_manifest
from nanvix_zutil.paths import dist_dir, nanvix_root, release_dir
from nanvix_zutil.release import package

HELP: str = "Package release archives from .nanvix/out/release into .nanvix/out/dist"
"""One-line description surfaced in ``nanvix-zutil --help``."""

_TARGET_ALLOWLIST = re.compile(r"^[A-Za-z0-9_.-]+$")


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``nanvix-zutil release``.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    return argparse.ArgumentParser(
        prog="nanvix-zutil release",
        description=(
            "Package release archives from .nanvix/out/release into"
            " .nanvix/out/dist. If .nanvix/z.py defines a release_targets()"
            " override, one archive is produced per entry; otherwise a"
            " single archive covers the whole release directory."
        ),
    )


def _check_target(target: str) -> None:
    """Reject release-target names that are not filename-safe."""
    if not _TARGET_ALLOWLIST.match(target) and target not in (".", ".."):
        log.fatal(
            f"Invalid release target '{target}'."
            " Characters must be alphanumeric, underscore, hyphen, or dot.",
            code=EXIT_INVALID_ARGS,
        )


def consumer_release_targets() -> dict[str, str]:
    """Return ``release_targets()`` from ``.nanvix/z.py`` if it defines one.

    Returns ``{}`` when ``z.py`` is absent or defines no ``release_targets``
    override.  Import errors on ``z.py`` propagate — a broken consumer
    script must fail loudly rather than silently ship a default archive.
    """
    z_py = nanvix_root() / "z.py"
    if not z_py.exists():
        return {}

    # Lazy import: ``__main__`` imports this module at top level for HELP.
    from nanvix_zutil.__main__ import discover_script_class

    script_cls = discover_script_class()
    instance = script_cls()

    targets_fn = getattr(instance, "release_targets", None)
    if not callable(targets_fn):
        return {}
    result = targets_fn()
    if not isinstance(result, dict):
        return {}
    return {str(k): str(v) for k, v in result.items()}  # type: ignore[reportUnknownVariableType]


def release() -> None:
    """Package release archives from ``.nanvix/out/release``."""
    manifest = load_manifest()
    config = Config()

    targets = consumer_release_targets()
    if not targets:
        name = (
            f"{manifest.name}"
            f"-{config.machine}"
            f"-{config.deployment_mode}"
            f"-{config.memory_size}"
        )
        package([release_dir()], dist_dir(), name)
        return

    for input, output in targets.items():
        _check_target(input)
        _check_target(output)
        package([release_dir() / input], dist_dir(), output)


def main() -> None:
    """Entry point for ``nanvix-zutil release``."""
    parser = _build_parser()
    _args = parser.parse_args()
    release()
    sys.exit(EXIT_SUCCESS)
