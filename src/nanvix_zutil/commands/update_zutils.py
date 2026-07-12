# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Atomically update zutils pins and verified bootstrap templates."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import cast

from nanvix_zutil import github
from nanvix_zutil.exitcodes import EXIT_GENERAL_ERROR, EXIT_INVALID_ARGS, EXIT_SUCCESS
from nanvix_zutil.updater import (
    UpdateError,
    UpdateResult,
    UpdateTransaction,
    emit_result,
    find_target_root,
    is_zutils_source,
    update_transaction,
)
from nanvix_zutil.utils import SEMVER_RE

HELP = "Atomically update zutils pins and verified bootstrap templates"

_TEMPLATE_ASSET = "templates.zip"
_TEMPLATE_NAMES = (
    ".zutils-version",
    "z",
    "z.sh",
    "z.ps1",
    ".gitignore",
)
_SOURCE_SENTINEL = "nanvix-ci.yml"
_SOURCE_NAMES = (*_TEMPLATE_NAMES, _SOURCE_SENTINEL)


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``update-zutils`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="nanvix-zutil update-zutils",
        description=(
            "Atomically update the zutils pin and bootstrap files from an exact"
            " release archive or a verified local templates source."
        ),
    )
    parser.add_argument("--to", required=True, metavar="VERSION")
    parser.add_argument(
        "--templates-dir",
        type=Path,
        help="Local zutils templates directory for offline/testing use.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    return parser


def _validate_template_map(
    templates: dict[str, bytes],
    target: str,
) -> dict[str, bytes]:
    """Validate a complete template source for the exact target release."""
    missing = sorted(set(_SOURCE_NAMES) - templates.keys())
    if missing:
        raise UpdateError(f"template source is missing: {', '.join(missing)}")
    result: dict[str, bytes] = {}
    for name in _SOURCE_NAMES:
        content = templates[name]
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UpdateError(f"template {name!r} is not UTF-8") from exc
        result[name] = content
    pin = result[".zutils-version"].decode("utf-8").strip()
    if pin != target:
        raise UpdateError(
            f"template source targets {pin!r}, expected exact release {target!r}"
        )
    if not result["z"].startswith(b"#!") or not result["z.sh"].startswith(b"#!"):
        raise UpdateError("z and z.sh templates must have shebangs")
    if not result["z.ps1"].strip():
        raise UpdateError("z.ps1 template is empty")
    if b"nanvix/workflows" not in result[_SOURCE_SENTINEL]:
        raise UpdateError("nanvix-ci.yml is not a zutils workflow template")
    return result


def _templates_from_directory(path: Path, target: str) -> dict[str, bytes]:
    """Read and verify a local zutils templates source."""
    try:
        root = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise UpdateError(f"templates directory does not exist: {path}") from exc
    if not root.is_dir():
        raise UpdateError(f"templates source is not a directory: {root}")
    templates: dict[str, bytes] = {}
    for name in _SOURCE_NAMES:
        source = root / name
        if not source.is_file() or source.is_symlink():
            raise UpdateError(f"invalid or missing template: {source}")
        templates[name] = source.read_bytes()
    return _validate_template_map(templates, target)


def _release_asset(release: dict[str, object], target: str) -> tuple[str, str]:
    """Return the unique exact template archive URL and SHA-256 digest."""
    if release.get("tag_name") != target:
        raise UpdateError("resolved zutils release tag does not match --to")
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise UpdateError("zutils release has no assets")
    matches: list[tuple[str, str]] = []
    for item in cast("list[object]", raw_assets):
        if not isinstance(item, dict):
            continue
        asset = cast("dict[str, object]", item)
        if asset.get("name") != _TEMPLATE_ASSET:
            continue
        url = asset.get("browser_download_url")
        digest = asset.get("digest")
        if isinstance(url, str) and isinstance(digest, str):
            matches.append((url, digest))
    if len(matches) != 1:
        raise UpdateError(f"zutils release must contain exactly one {_TEMPLATE_ASSET}")
    url, digest = matches[0]
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise UpdateError("zutils template asset has no valid SHA-256 digest")
    return url, digest


def _download_archive(url: str, gh_token: str | None) -> bytes:
    """Download a release template archive into memory."""
    headers = {"Accept": "application/octet-stream", "User-Agent": "nanvix-zutil"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30.0) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UpdateError(f"failed to download zutils templates: {exc}") from exc


def _verify_archive_digest(data: bytes, expected: str) -> None:
    """Require downloaded archive bytes to match GitHub's asset digest."""
    actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if actual != expected:
        raise UpdateError(
            f"zutils template archive digest mismatch: expected {expected},"
            f" got {actual}"
        )


def _templates_from_archive(data: bytes, target: str) -> dict[str, bytes]:
    """Safely extract required templates from an in-memory ZIP archive."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateError("zutils template archive is malformed") from exc
    templates: dict[str, bytes] = {}
    with archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if info.is_dir():
                continue
            if path.is_absolute() or ".." in path.parts:
                raise UpdateError("zutils template archive contains an unsafe path")
            name = path.name
            if name not in _SOURCE_NAMES:
                continue
            if name in templates:
                raise UpdateError(f"duplicate template {name!r} in archive")
            if info.external_attr >> 16 & 0o170000 == 0o120000:
                raise UpdateError(f"template {name!r} is a symlink")
            templates[name] = archive.read(info)
    return _validate_template_map(templates, target)


def _resolve_templates(
    target: str,
    templates_dir: Path | None,
) -> dict[str, bytes]:
    """Resolve local templates or the exact release archive."""
    if templates_dir is not None:
        return _templates_from_directory(templates_dir, target)
    gh_token = os.environ.get("GH_TOKEN")
    release = github.resolve_release("nanvix/zutils", target, gh_token=gh_token)
    url, digest = _release_asset(release, target)
    archive = _download_archive(url, gh_token)
    _verify_archive_digest(archive, digest)
    return _templates_from_archive(archive, target)


def _target_maps(root: Path) -> list[dict[str, Path]]:
    """Return exact source-name to target-path maps."""
    if not is_zutils_source(root):
        return [
            {
                ".zutils-version": Path(".zutils-version"),
                "z": Path("z"),
                "z.sh": Path("z.sh"),
                "z.ps1": Path("z.ps1"),
                ".gitignore": Path(".nanvix/.gitignore"),
            }
        ]
    maps: list[dict[str, Path]] = [
        {
            ".zutils-version": Path("templates/.zutils-version"),
            "z": Path("templates/z"),
            "z.sh": Path("templates/z.sh"),
            "z.ps1": Path("templates/z.ps1"),
            ".gitignore": Path("templates/.gitignore"),
        }
    ]
    for example in ("bin-hello", "lib-hello"):
        base = Path("examples") / example
        maps.append(
            {
                ".zutils-version": base / ".zutils-version",
                "z": base / "z",
                "z.sh": base / "z.sh",
                "z.ps1": base / "z.ps1",
                ".gitignore": base / ".nanvix/.gitignore",
            }
        )
    return maps


def _line_ending(content: bytes) -> bytes:
    """Return the existing target's line-ending policy."""
    return b"\r\n" if b"\r\n" in content else b"\n"


def _convert_line_endings(content: bytes, newline: bytes) -> bytes:
    """Normalize text to one target-specific line ending."""
    normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return normalized.replace(b"\n", newline)


def _run_locked(
    args: argparse.Namespace,
    root: Path,
    transaction: UpdateTransaction,
) -> tuple[UpdateResult, int]:
    """Plan and apply a zutils template update under its transaction lock."""
    version = args.to.removeprefix("v")
    if SEMVER_RE.fullmatch(version) is None:
        raise UpdateError("--to must be a semantic version (vX.Y.Z or X.Y.Z)")
    target = f"v{version}"
    templates = _resolve_templates(target, args.templates_dir)
    candidates: dict[Path, bytes] = {}
    validators: dict[Path, Callable[[bytes], None]] = {}
    create_modes: dict[Path, int] = {}
    for target_map in _target_maps(root):
        for source_name, relative in target_map.items():
            path = root / relative
            current = path.read_bytes() if path.is_file() else templates[source_name]
            newline = _line_ending(current)
            candidate = _convert_line_endings(templates[source_name], newline)
            candidates[relative] = candidate
            if source_name in {"z", "z.sh"}:
                create_modes[relative] = 0o755

            def validate(
                value: bytes,
                expected: bytes = candidate,
                label: str = relative.as_posix(),
            ) -> None:
                if value != expected:
                    raise UpdateError(f"{label}: invalid template candidate")

            validators[relative] = validate
    if args.dry_run or args.check:
        changed = tuple(
            sorted(
                path.as_posix()
                for path, content in candidates.items()
                if not (root / path).is_file() or (root / path).read_bytes() != content
            )
        )
        for path, validator in validators.items():
            validator(candidates[path])
    else:
        changed = transaction.install(
            candidates,
            validators,
            create_modes=create_modes,
        )
    status = (
        "up-to-date"
        if not changed
        else "outdated" if args.check else "would-update" if args.dry_run else "updated"
    )
    result = UpdateResult("update-zutils", status, target, changed)
    return result, EXIT_GENERAL_ERROR if args.check and changed else EXIT_SUCCESS


def _run(args: argparse.Namespace) -> tuple[UpdateResult, int]:
    """Recover managed files before parsing and hold the update lock."""
    if args.check and args.dry_run:
        raise UpdateError("--check and --dry-run are mutually exclusive")
    root = find_target_root()
    with update_transaction(root) as transaction:
        return _run_locked(args, root, transaction)


def main() -> None:
    """Entry point for ``nanvix-zutil update-zutils``."""
    parser = _build_parser()
    args = parser.parse_args()
    try:
        result, code = _run(args)
        emit_result(
            result,
            output_format=args.output_format,
            output=args.output,
        )
        sys.exit(code)
    except (OSError, UpdateError) as exc:
        if args.output_format == "json":
            print(
                json.dumps(
                    {
                        "level": "error",
                        "code": EXIT_INVALID_ARGS,
                        "message": str(exc),
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)


if __name__ == "__main__":
    main()
