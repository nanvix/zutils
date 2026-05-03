# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-zutil resolve`` — resolve manifest and emit CI-ready metadata."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP, EXIT_SUCCESS
from nanvix_zutil.manifest import load_manifest
from nanvix_zutil.resolver import resolve

HELP: str = "Resolve manifest and emit CI-ready metadata"
"""One-line description surfaced in ``nanvix-zutil --help``."""


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for ``nanvix-zutil resolve``.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="nanvix-zutil resolve",
        description=(
            "Resolve the .nanvix/nanvix.toml manifest and emit release "
            "metadata as key=value lines (suitable for $GITHUB_OUTPUT) "
            "or JSON."
        ),
    )
    parser.add_argument(
        "--manifest",
        default=".nanvix/nanvix.toml",
        metavar="PATH",
        help="Path to the nanvix.toml manifest (default: .nanvix/nanvix.toml)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit a single JSON object instead of key=value lines",
    )
    parser.add_argument(
        "--shallow",
        action="store_true",
        default=False,
        help="Resolve only direct dependencies (skip transitive discovery)",
    )
    parser.add_argument(
        "--gh-token",
        default=None,
        metavar="TOKEN",
        dest="gh_token",
        help="GitHub personal access token (overrides GH_TOKEN env var)",
    )
    return parser


def main() -> None:
    """Entry point for ``nanvix-zutil resolve``."""
    parser = _build_parser()
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        log.fatal(
            f"Manifest not found: {manifest_path}",
            code=EXIT_MISSING_DEP,
            hint="Run from a Nanvix consumer repo root, or pass --manifest.",
        )

    gh_token: str | None = args.gh_token or os.environ.get("GH_TOKEN")

    if args.json:
        log.set_json_mode(True)

    manifest = load_manifest(manifest_path)
    lockfile = resolve(
        manifest,
        gh_token=gh_token,
        shallow=args.shallow,
        manifest_path=manifest_path,
    )

    sysroot = next(
        (p for p in lockfile.packages if p.kind == "sysroot"),
        None,
    )
    if sysroot is None:
        log.fatal(
            "No sysroot package in resolved lockfile",
            code=EXIT_MISSING_DEP,
            hint="Check that nanvix-version is set in nanvix.toml.",
        )

    result = {
        "nanvix_tag": sysroot.resolved_tag,
        "nanvix_sha": sysroot.resolved_commitish[:7],
        "nanvix_version": sysroot.resolved_tag.lstrip("v"),
        "package_name": manifest.name,
        "package_version": manifest.version,
    }

    if args.json:
        log.set_json_mode(False)
        print(json.dumps(result))
    else:
        for key, value in result.items():
            print(f"{key}={value}")

    sys.exit(EXIT_SUCCESS)
