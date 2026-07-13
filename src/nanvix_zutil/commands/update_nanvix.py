# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Atomically update coherent Nanvix SDK, runtime, and dependency pins."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

from nanvix_zutil.buildroot import Dependency, Ref, RefKind
from nanvix_zutil.exitcodes import (
    EXIT_GENERAL_ERROR,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
    EXIT_SUCCESS,
)
from nanvix_zutil.lockfile import serialize_lockfile
from nanvix_zutil.manifest import (
    Manifest,
    SdkPin,
    Toolchain,
    ToolchainKind,
    load_manifest,
)
from nanvix_zutil.resolver import BlockedResolution, resolve
from nanvix_zutil.sdk import SdkRelease, resolve_sdk_release, verify_sdk_image
from nanvix_zutil.updater import (
    UpdateTransaction,
    UpdateError,
    UpdateResult,
    emit_result,
    find_target_root,
    is_zutils_source,
    load_sdk_target,
    update_transaction,
)

HELP = "Atomically update verified Nanvix SDK, runtime, and dependency pins"

_VERSION_LINE = re.compile(
    r'^(?P<prefix>\s*nanvix-version\s*=\s*")[^"]*(?P<suffix>".*)$',
    re.MULTILINE,
)
_SDK_ASSIGNMENT = re.compile(
    r'^(?P<prefix>\s*NANVIX_SDK_IMAGE\s*(?::\s*str\s*)?=\s*")[^"]*(?P<suffix>".*)$',
    re.MULTILINE,
)
_DOCKER_IMAGE_KEY = re.compile(
    r"^(?P<indent>[ \t]*)docker-image[ \t]*:[ \t]*(?P<value>.*)$",
)


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``update-nanvix`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="nanvix-zutil update-nanvix",
        description=(
            "Resolve an authoritative SDK release, build a strict dependency"
            " lock, and atomically update all coherent local pins."
        ),
    )
    parser.add_argument(
        "--to",
        metavar="SDK_TAG_OR_CONTRACT",
        help="SDK tag, local sdk-release.json, or inline contract."
        " Defaults to the latest authoritative SDK GitHub Release.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    return parser


def _paths(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Return exact manifest and optional transitional-marker paths."""
    if is_zutils_source(root):
        return (
            [
                Path("examples/bin-hello/.nanvix/nanvix.toml"),
                Path("examples/lib-hello/.nanvix/nanvix.toml"),
            ],
            [
                Path("examples/bin-hello/.nanvix/z.py"),
                Path("examples/lib-hello/.nanvix/z.py"),
            ],
            [Path("templates/nanvix-ci.yml")],
        )
    return (
        [Path(".nanvix/nanvix.toml")],
        [Path(".nanvix/z.py")],
        [Path(".github/workflows/nanvix-ci.yml")],
    )


def _replace_exact(
    pattern: re.Pattern[str],
    text: str,
    value: str,
    label: str,
) -> str:
    """Replace exactly one quoted assignment."""
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise UpdateError(
            f"{label}: expected exactly one assignment, found {len(matches)}"
        )
    return pattern.sub(
        lambda match: f'{match.group("prefix")}{value}{match.group("suffix")}',
        text,
        count=1,
    )


def _remove_docker_image_inputs(text: str) -> str | None:
    """Remove inline or block-scalar caller image inputs from workflow YAML."""
    lines = text.splitlines(keepends=True)
    rendered: list[str] = []
    removed = False
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _DOCKER_IMAGE_KEY.match(line.rstrip("\r\n"))
        if match is None:
            rendered.append(line)
            index += 1
            continue

        removed = True
        marker = match.group("value").split("#", maxsplit=1)[0].strip()
        base_indent = len(match.group("indent").expandtabs(8))
        index += 1
        if marker.startswith((">", "|")):
            while index < len(lines):
                candidate = lines[index].rstrip("\r\n")
                if not candidate.strip():
                    rendered.append(lines[index])
                    index += 1
                    continue
                prefix_length = len(candidate) - len(candidate.lstrip(" \t"))
                indent = len(candidate[:prefix_length].expandtabs(8))
                if indent <= base_indent:
                    break
                index += 1

    return "".join(rendered) if removed else None


def _canonical_toolchain_block(
    text: str,
    sdk: SdkRelease,
    existing_pin: SdkPin | None = None,
) -> str:
    """Replace or append the canonical ``[toolchain]`` SDK table."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    header = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?(?:\r?\n)?$")
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = header.match(line)
        if match is None:
            continue
        if start is None and match.group(1).strip() == "toolchain":
            start = index
            continue
        if start is not None:
            if match.group(1).strip().startswith("toolchain."):
                continue
            end = index
            break
    block = (
        f"[toolchain]{newline}"
        f'kind = "nanvix-sdk"{newline}'
        f'provider = "{sdk.provider_id}"{newline}'
        f'sdk-version = "{sdk.sdk_version}"{newline}'
        f'sdk-image = "{sdk.image.name}"{newline}'
        f'sdk-digest = "{sdk.image.digest}"{newline}'
    )
    if (
        existing_pin is not None
        and existing_pin.build_image is not None
        and existing_pin.build_digest is not None
    ):
        block += (
            f'build-image = "{existing_pin.build_image}"{newline}'
            f'build-digest = "{existing_pin.build_digest}"{newline}'
        )
    if start is None:
        separator = "" if text.endswith(("\n", "\r")) else newline
        return f"{text}{separator}{newline}{block}"
    if end < len(lines):
        block += newline
    lines[start:end] = [block]
    return "".join(lines)


def _update_manifest(
    content: bytes,
    sdk: SdkRelease,
    existing_pin: SdkPin | None = None,
) -> bytes:
    """Create the complete canonical manifest candidate."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdateError("nanvix.toml is not UTF-8") from exc
    text = _replace_exact(
        _VERSION_LINE,
        text,
        sdk.runtime_version,
        "nanvix.toml",
    )
    return _canonical_toolchain_block(text, sdk, existing_pin).encode("utf-8")


def _sdk_pin(
    sdk: SdkRelease,
    existing_pin: SdkPin | None = None,
) -> SdkPin:
    """Build the typed manifest pin for a verified release."""
    return SdkPin(
        version=sdk.sdk_version,
        provider_id=sdk.provider_id,
        image=sdk.image.name,
        digest=sdk.image.digest,
        build_image=existing_pin.build_image if existing_pin is not None else None,
        build_digest=(existing_pin.build_digest if existing_pin is not None else None),
    )


def _retarget_dependency(dep: Dependency, sdk: SdkRelease) -> Dependency:
    """Retarget a version dependency to the selected SDK revision."""
    if dep.ref.kind != RefKind.VERSION or not isinstance(dep.ref.value, str):
        return dep
    base = dep.ref.value.split("-nanvix-", maxsplit=1)[0]
    return replace(
        dep,
        ref=Ref(
            RefKind.VERSION,
            f"{base}-nanvix-{sdk.sdk_version.removeprefix('v')}",
        ),
    )


def _candidate_manifest(
    current: Manifest,
    sdk: SdkRelease,
) -> Manifest:
    """Retarget a parsed manifest to a verified SDK release."""
    return replace(
        current,
        sysroot_ref=Ref(RefKind.TAG, sdk.runtime_version),
        dependencies=[_retarget_dependency(dep, sdk) for dep in current.dependencies],
        system_dependencies=[
            _retarget_dependency(dep, sdk) for dep in current.system_dependencies
        ],
        toolchain=Toolchain(
            ToolchainKind.SDK,
            _sdk_pin(sdk, current.toolchain.sdk),
        ),
    )


def _validate_manifest_candidate(
    content: bytes,
    sdk: SdkRelease,
    pin: SdkPin | None = None,
) -> None:
    """Validate the canonical manifest table before any target write."""
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UpdateError(f"candidate nanvix.toml is invalid: {exc}") from exc
    package = data.get("package")
    if (
        not isinstance(package, dict)
        or cast("dict[str, object]", package).get("nanvix-version")
        != sdk.runtime_version
    ):
        raise UpdateError("candidate manifest runtime does not match the SDK")
    expected: dict[str, object] = {
        "kind": "nanvix-sdk",
        "provider": sdk.provider_id,
        "sdk-version": sdk.sdk_version,
        "sdk-image": sdk.image.name,
        "sdk-digest": sdk.image.digest,
    }
    if pin is not None and pin.build_image is not None and pin.build_digest is not None:
        expected["build-image"] = pin.build_image
        expected["build-digest"] = pin.build_digest
    if data.get("toolchain") != expected:
        raise UpdateError("candidate manifest toolchain table is not canonical")


def _tuple_from_manifest(manifest: Manifest) -> dict[str, object]:
    """Return the current coherent pin tuple for machine output."""
    pin = manifest.toolchain.sdk
    return {
        "nanvix_version": str(manifest.sysroot_ref.value).removeprefix("v"),
        "sdk_version": pin.version if pin is not None else None,
        "sdk_image": pin.image_ref if pin is not None else None,
        "build_image": pin.effective_build_ref if pin is not None else None,
    }


def _tuple_from_sdk(sdk: SdkRelease) -> dict[str, object]:
    """Return the selected coherent pin tuple for machine output."""
    return {
        "nanvix_version": sdk.runtime_version,
        "nanvix_tag": cast(str, sdk.libc["nanvix_tag"]),
        "sdk_version": sdk.sdk_version,
        "sdk_image": sdk.image.ref,
        "build_image": sdk.image.ref,
        "provider": sdk.provider_id,
    }


def _resolve_target(value: str | None, root: Path, gh_token: str | None) -> SdkRelease:
    """Resolve local/inline input or an authoritative GitHub SDK Release."""
    if value is not None:
        local = load_sdk_target(value, root)
        if local is not None:
            verify_sdk_image(local, pull=False)
            return local
    return resolve_sdk_release(
        value or "latest",
        gh_token=gh_token,
        verify_image=True,
    )


def _optional_marker_candidates(
    root: Path,
    scripts: list[Path],
    workflows: list[Path],
    old_images: set[str],
    new_image: str,
) -> tuple[dict[Path, bytes], dict[Path, Callable[[bytes], None]]]:
    """Update transitional image markers only where they already exist."""
    candidates: dict[Path, bytes] = {}
    validators: dict[Path, Callable[[bytes], None]] = {}
    for relative in scripts:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8")
        try:
            ast.parse(text, filename=relative.as_posix())
        except SyntaxError as exc:
            raise UpdateError(f"{relative}: invalid Python: {exc}") from exc
        matches = list(_SDK_ASSIGNMENT.finditer(text))
        if len(matches) > 1:
            raise UpdateError(f"{relative}: multiple transitional SDK markers")
        if not matches:
            continue
        current = matches[0].group(0)
        prefix = len(matches[0].group("prefix"))
        suffix = len(matches[0].group("suffix"))
        current_image = current[prefix : len(current) - suffix]
        if old_images and current_image not in old_images:
            raise UpdateError(f"{relative}: SDK marker disagrees with the manifest")
        candidate = _replace_exact(
            _SDK_ASSIGNMENT,
            text,
            new_image,
            relative.as_posix(),
        ).encode("utf-8")
        candidates[relative] = candidate

        def validate_script(
            value: bytes,
            label: str = relative.as_posix(),
            expected: str = new_image,
        ) -> None:
            rendered = value.decode("utf-8")
            ast.parse(rendered, filename=label)
            matches = list(_SDK_ASSIGNMENT.finditer(rendered))
            if len(matches) != 1 or expected not in matches[0].group(0):
                raise UpdateError(f"{label}: invalid transitional SDK marker")

        validators[relative] = validate_script

    for relative in workflows:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8")
        candidate_text = _remove_docker_image_inputs(text)
        if candidate_text is None:
            continue
        candidate = candidate_text.encode("utf-8")
        candidates[relative] = candidate

        def validate_workflow(
            value: bytes,
            label: str = relative.as_posix(),
        ) -> None:
            rendered = value.decode("utf-8")
            if any(
                _DOCKER_IMAGE_KEY.match(line) is not None
                for line in rendered.splitlines()
            ):
                raise UpdateError(f"{label}: redundant docker-image input remains")

        validators[relative] = validate_workflow
    return candidates, validators


def _run_locked(
    args: argparse.Namespace,
    root: Path,
    transaction: UpdateTransaction,
) -> tuple[UpdateResult, int]:
    """Resolve and apply an SDK update under a recovered transaction lock."""
    gh_token = os.environ.get("GH_TOKEN")
    sdk = _resolve_target(args.to, root, gh_token)
    manifests, scripts, workflows = _paths(root)
    candidates: dict[Path, bytes] = {}
    validators: dict[Path, Callable[[bytes], None]] = {}
    old_tuples: list[dict[str, object]] = []
    old_images: set[str] = set()
    new_build_images: set[str] = set()
    new_tuple = _tuple_from_sdk(sdk)

    for relative in manifests:
        path = root / relative
        current = load_manifest(path)
        old_tuples.append(_tuple_from_manifest(current))
        current_pin = current.toolchain.sdk
        if (
            current_pin is not None
            and current_pin.build_image is not None
            and current_pin.version != sdk.sdk_version
        ):
            return (
                UpdateResult(
                    command="update-nanvix",
                    status="blocked",
                    target=sdk.sdk_version,
                    changed_files=(),
                    old={"manifests": old_tuples},
                    new=new_tuple,
                    blocker={
                        "status": "blocked",
                        "reason": "derived-build-image-update-required",
                        "build_image": current_pin.effective_build_ref,
                    },
                ),
                EXIT_MISSING_DEP,
            )
        if current.toolchain.sdk is not None:
            old_images.add(current.toolchain.sdk.effective_build_ref)
        manifest_bytes = _update_manifest(path.read_bytes(), sdk, current_pin)
        candidate_pin = _sdk_pin(sdk, current_pin)
        new_build_images.add(candidate_pin.effective_build_ref)
        _validate_manifest_candidate(manifest_bytes, sdk, candidate_pin)
        candidate_manifest = _candidate_manifest(current, sdk)
        cache_dir = path.parent / "cache" / f"update-nanvix-{os.getpid()}"
        try:
            lock = resolve(
                candidate_manifest,
                gh_token=gh_token,
                cache_dir=cache_dir,
                strict=True,
                verified_sdk_release=sdk,
                manifest_content=manifest_bytes,
            )
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)
        if isinstance(lock, BlockedResolution):
            result = UpdateResult(
                command="update-nanvix",
                status="blocked",
                target=sdk.sdk_version,
                changed_files=(),
                old={"manifests": old_tuples},
                new=new_tuple,
                blocker=lock.to_dict(),
            )
            return result, EXIT_MISSING_DEP
        lock_relative = relative.with_name("nanvix.lock")
        lock_bytes = serialize_lockfile(lock)
        candidates[relative] = manifest_bytes
        candidates[lock_relative] = lock_bytes

        def validate_manifest(
            value: bytes,
            release: SdkRelease = sdk,
            expected_pin: SdkPin = candidate_pin,
        ) -> None:
            _validate_manifest_candidate(value, release, expected_pin)

        def validate_lock(
            value: bytes,
            release: SdkRelease = sdk,
        ) -> None:
            try:
                parsed = tomllib.loads(value.decode("utf-8"))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise UpdateError(f"candidate nanvix.lock is invalid: {exc}") from exc
            metadata = parsed.get("metadata")
            if not isinstance(metadata, dict):
                raise UpdateError("candidate lock is missing metadata")
            sdk_data = cast("dict[str, object]", metadata).get("sdk")
            if (
                not isinstance(sdk_data, dict)
                or cast("dict[str, object]", sdk_data).get("sdk-version")
                != release.sdk_version
            ):
                raise UpdateError("candidate lock is missing SDK provenance")

        validators[relative] = validate_manifest
        validators[lock_relative] = validate_lock

    if len(new_build_images) != 1:
        raise UpdateError("candidate manifests disagree on the effective build image")
    new_build_image = next(iter(new_build_images))
    new_tuple["build_image"] = new_build_image
    marker_candidates, marker_validators = _optional_marker_candidates(
        root,
        scripts,
        workflows,
        old_images,
        new_build_image,
    )
    candidates.update(marker_candidates)
    validators.update(marker_validators)

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
        changed = transaction.install(candidates, validators)
    status = (
        "up-to-date"
        if not changed
        else "outdated" if args.check else "would-update" if args.dry_run else "updated"
    )
    result = UpdateResult(
        command="update-nanvix",
        status=status,
        target=sdk.sdk_version,
        changed_files=changed,
        old={"manifests": old_tuples},
        new=new_tuple,
    )
    return result, EXIT_GENERAL_ERROR if args.check and changed else EXIT_SUCCESS


def _run(args: argparse.Namespace) -> tuple[UpdateResult, int]:
    """Recover managed files before parsing and hold the update lock."""
    if args.check and args.dry_run:
        raise UpdateError("--check and --dry-run are mutually exclusive")
    root = find_target_root()
    with update_transaction(root) as transaction:
        return _run_locked(args, root, transaction)


def main() -> None:
    """Entry point for ``nanvix-zutil update-nanvix``."""
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
