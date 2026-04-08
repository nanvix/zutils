# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil matrix`` — emit the build matrix as CI-ready JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP, EXIT_SUCCESS
from nanvix_zutil.manifest import load_manifest


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``nanvix-zutil matrix``.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="nanvix-zutil matrix",
        description=(
            "Read the [builds] section from .nanvix/nanvix.toml and emit "
            "the build matrix as a JSON object suitable for GitHub Actions."
        ),
    )
    parser.add_argument(
        "--manifest",
        default=".nanvix/nanvix.toml",
        metavar="PATH",
        help="Path to the nanvix.toml manifest (default: .nanvix/nanvix.toml)",
    )
    return parser


def _matrix_to_json(manifest_path: Path) -> dict[str, object]:
    """Load the manifest and convert the build matrix to a JSON-ready dict.

    Args:
        manifest_path: Path to the ``nanvix.toml`` file.

    Returns:
        Dictionary with ``platforms``, ``modes``, ``memory``, and
        optionally ``exclude`` keys.
    """
    manifest = load_manifest(manifest_path)
    builds = manifest.builds

    result: dict[str, object] = {}
    result["platforms"] = builds.dimensions.get("platforms", [])
    result["modes"] = builds.dimensions.get("modes", [])
    result["memory"] = builds.dimensions.get("memory", [])
    if builds.exclude:
        result["exclude"] = builds.exclude

    return result


def main() -> None:
    """Entry point for ``nanvix-zutil matrix``."""
    parser = _build_parser()
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        log.fatal(
            f"Manifest not found: {manifest_path}",
            code=EXIT_MISSING_DEP,
            hint="Run from a Nanvix consumer repo root, or pass --manifest.",
        )

    result = _matrix_to_json(manifest_path)
    print(json.dumps(result))

    sys.exit(EXIT_SUCCESS)
