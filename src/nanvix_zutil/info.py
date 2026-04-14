# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""``nanvix-info`` CLI command.

Queries the GitHub Releases API for a Nanvix release, extracts the short
commit SHA embedded in the sysroot asset filename, and optionally resolves
the semver version from the release name.  Output is either a series of
``key=value`` lines (suitable for ``>> $GITHUB_OUTPUT`` in CI) or a single
JSON object (``--json``).

Example usage::

    nanvix-info
    nanvix-info --version latest --machine microvm --mode standalone
    nanvix-info --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import cast

from nanvix_zutil import log
from nanvix_zutil.config import (
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_MACHINE,
    DEFAULT_MEMORY_SIZE,
    DEFAULT_TARGET,
)
from nanvix_zutil.exitcodes import (
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
    EXIT_NETWORK_ERROR,
    EXIT_SUCCESS,
)
from nanvix_zutil.github import resolve_release
from nanvix_zutil.utils import SEMVER_RE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_REPO = "nanvix/nanvix"
_DEFAULT_VERSION = "latest"

# Sysroot asset prefix pattern: nanvix-{target}-{machine}-{mode}-release-{mem}-{sha}.tar.bz2
_ASSET_RE = re.compile(
    r"^nanvix-(?P<target>[^-]+)-(?P<machine>[^-]+-?[^-]*)-(?P<mode>.+)-release-"
    r"(?P<mem>[^-]+)-(?P<sha>[0-9a-fA-F]{4,40})\.tar\.bz2$"
)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NanvixInfo:
    """Release information resolved from a Nanvix GitHub release.

    Attributes:
        tag: The GitHub release tag (e.g. ``"v0.4.1"``).
        sha: The short commit SHA embedded in the sysroot asset filename
            (e.g. ``"fa06b88"``).
        version: The semver string extracted from the release name, or
            ``None`` when the release name contains no semver.
    """

    tag: str
    sha: str
    version: str | None

    def to_dict(self) -> dict[str, str]:
        """Return a plain dictionary of the non-None fields.

        Returns:
            Mapping of ``tag``, ``sha``, and optionally ``version``.
        """
        d: dict[str, str] = {"tag": self.tag, "sha": self.sha}
        if self.version is not None:
            d["version"] = self.version
        return d


# ---------------------------------------------------------------------------
# Resolution logic
# ---------------------------------------------------------------------------


def _extract_sha(
    assets: list[object],
    target: str,
    machine: str,
    mode: str,
    memory: str,
) -> str | None:
    """Scan *assets* for the sysroot archive and extract its SHA.

    Args:
        assets: List of asset dictionaries from the GitHub API.
        target: Target architecture (e.g. ``"x86"``).
        machine: Target machine identifier (e.g. ``"microvm"``).
        mode: Deployment mode (e.g. ``"standalone"``).
        memory: Memory size string (e.g. ``"256mb"``).

    Returns:
        The short commit SHA (e.g. ``"fa06b88"``) or ``None`` if no matching
        asset was found.
    """
    prefix = f"nanvix-{target}-{machine}-{mode}-release-{memory}-"
    for raw_item in assets:
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)
        name = item.get("name")
        if not isinstance(name, str):
            continue
        if not name.startswith(prefix):
            continue
        m = _ASSET_RE.match(name)
        if m:
            return m.group("sha")
    return None


def _extract_version(release_name: object) -> str | None:
    """Extract a semver string from *release_name* (the release ``name`` field).

    Scans the release name for a ``MAJOR.MINOR.PATCH`` pattern.

    Args:
        release_name: Raw ``name`` field from the GitHub API release object.

    Returns:
        The first semver string found, or ``None``.
    """
    if not isinstance(release_name, str):
        return None
    for token in release_name.split():
        token = token.strip("v")
        if SEMVER_RE.match(token):
            return token
    return None


def get_nanvix_info(
    repo: str = _DEFAULT_REPO,
    version: str = _DEFAULT_VERSION,
    target: str = DEFAULT_TARGET,
    machine: str = DEFAULT_MACHINE,
    mode: str = DEFAULT_DEPLOYMENT_MODE,
    memory: str = DEFAULT_MEMORY_SIZE,
    gh_token: str | None = None,
) -> NanvixInfo:
    """Resolve Nanvix release information.

    Fetches the specified release from GitHub, extracts the commit SHA from
    the sysroot asset filename, and optionally resolves the semver version
    from the release name.

    Args:
        repo: Repository in ``owner/name`` format (default: ``"nanvix/nanvix"``).
        version: Release tag, ``"latest"``, semver, or commit hash
            (default: ``"latest"``).
        target: Target architecture (default: ``"x86"``).
        machine: Target machine identifier (default: ``"microvm"``).
        mode: Deployment mode (default: ``"standalone"``).
        memory: Memory size string (default: ``"256mb"``).
        gh_token: Optional GitHub personal access token.

    Returns:
        A :class:`NanvixInfo` with ``tag``, ``sha``, and optional ``version``.

    Raises:
        SystemExit: On network failure or when the sysroot asset is not found
            in the release.
    """
    release = resolve_release(repo, version, gh_token, semver=True)

    tag_raw = release.get("tag_name")
    if not isinstance(tag_raw, str) or not tag_raw:
        log.fatal(
            f"Release from {repo}@{version} has no 'tag_name'",
            code=EXIT_NETWORK_ERROR,
        )
    tag: str = tag_raw

    raw_assets = release.get("assets", [])
    if not isinstance(raw_assets, list):
        log.fatal(
            f"Unexpected assets format in release {repo}@{version}",
            code=EXIT_NETWORK_ERROR,
        )
    assets: list[object] = cast(list[object], raw_assets)

    sha = _extract_sha(assets, target, machine, mode, memory)
    if sha is None:
        prefix = f"nanvix-{target}-{machine}-{mode}-release-{memory}-"
        log.fatal(
            f"No sysroot asset matching prefix '{prefix}' found in {repo}@{version}",
            code=EXIT_MISSING_DEP,
            hint=(
                "Check that --target, --machine, --mode, and --memory match "
                "an asset in the release."
            ),
        )

    rel_version = _extract_version(release.get("name"))

    return NanvixInfo(tag=tag, sha=sha, version=rel_version)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser(prog: str = "nanvix-info") -> argparse.ArgumentParser:
    """Build and return the argument parser for ``nanvix-info``.

    Args:
        prog: Program name shown in ``--help`` output.  Defaults to
            ``"nanvix-info"`` for standalone use; the ``nanvix-zutil``
            CLI passes ``"nanvix-zutil info"`` for integrated help.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Query a Nanvix GitHub release and emit the release tag, "
            "sysroot commit SHA, and optional semver version."
        ),
    )
    parser.add_argument(
        "--repo",
        default=_DEFAULT_REPO,
        metavar="OWNER/REPO",
        help=f"GitHub repository to query (default: {_DEFAULT_REPO})",
    )
    parser.add_argument(
        "--version",
        default=_DEFAULT_VERSION,
        metavar="VER",
        help=(
            "Release version specifier: tag name, 'latest', semver, or "
            f"commit hash (default: {_DEFAULT_VERSION})"
        ),
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        metavar="TARGET",
        help=f"Target architecture (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--machine",
        default=DEFAULT_MACHINE,
        metavar="MACH",
        help=f"Target machine identifier (default: {DEFAULT_MACHINE})",
    )
    parser.add_argument(
        "--mode",
        default=DEFAULT_DEPLOYMENT_MODE,
        metavar="MODE",
        help=f"Deployment mode (default: {DEFAULT_DEPLOYMENT_MODE})",
    )
    parser.add_argument(
        "--memory",
        default=DEFAULT_MEMORY_SIZE,
        metavar="MEM",
        help=f"Memory size string used in asset names (default: {DEFAULT_MEMORY_SIZE})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit a single JSON object instead of key=value lines",
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
    """Entry point for the ``nanvix-info`` command.

    Parses command-line arguments, resolves release information, and prints
    the result to stdout as either ``key=value`` lines or JSON.
    """
    parser = _build_parser()
    args = parser.parse_args()

    # Validate --repo
    if "/" not in args.repo or args.repo.count("/") != 1:
        log.fatal(
            f"Invalid repository format: '{args.repo}'. Expected 'owner/repo'.",
            code=EXIT_INVALID_ARGS,
        )

    gh_token: str | None = args.gh_token or os.environ.get("GH_TOKEN")

    if args.json:
        log.set_json_mode(True)

    info = get_nanvix_info(
        repo=args.repo,
        version=args.version,
        target=args.target,
        machine=args.machine,
        mode=args.mode,
        memory=args.memory,
        gh_token=gh_token,
    )

    if args.json:
        # Emit a single JSON object to stdout (not via log machinery).
        log.set_json_mode(False)
        print(json.dumps(info.to_dict()))
    else:
        for key, value in info.to_dict().items():
            print(f"{key}={value}")

    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
