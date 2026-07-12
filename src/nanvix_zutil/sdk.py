# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix SDK release coordinates and provenance verification."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import cast

from nanvix_zutil import github
from nanvix_zutil.exitcodes import (
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
    EXIT_NETWORK_ERROR,
)
from nanvix_zutil.log import fatal

SDK_RELEASE_ASSET = "sdk-release.json"
SDK_REPOSITORY = "nanvix/sdk"
SDK_LABEL_PREFIX = "dev.nanvix.sdk."

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SDK_VERSION_RE = re.compile(
    r"^v?(?P<runtime>[0-9]+\.[0-9]+\.[0-9]+)-sdk\.(?P<revision>[1-9][0-9]*)$"
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED_TOP_LEVEL = frozenset(
    {
        "schema_version",
        "sdk_version",
        "provider_id",
        "provider",
        "role",
        "image",
        "target",
        "toolchain",
        "libc",
        "compat",
        "features",
    }
)


class SdkValidationError(ValueError):
    """Raised when SDK release provenance is malformed or inconsistent."""


@dataclass(frozen=True)
class SdkImage:
    """Immutable SDK image coordinate."""

    name: str
    digest: str
    ref: str


@dataclass(frozen=True)
class SdkProvenance:
    """Verified SDK provenance persisted in a consumer lockfile."""

    sdk_version: str
    provider_id: str
    provider: str
    role: str
    image: SdkImage
    nanvix_tag: str
    nanvix_version: str
    nanvix_commit: str
    sysroot_sha256: str
    compat: dict[str, object]
    schema_version: int = 1
    target: dict[str, object] = field(default_factory=lambda: dict[str, object]())
    toolchain: dict[str, object] = field(default_factory=lambda: dict[str, object]())
    features: dict[str, object] = field(default_factory=lambda: dict[str, object]())


@dataclass(frozen=True)
class SdkRelease:
    """Validated ``sdk-release.json`` contract."""

    schema_version: int
    sdk_version: str
    provider_id: str
    provider: str
    role: str
    image: SdkImage
    target: dict[str, object]
    toolchain: dict[str, object]
    libc: dict[str, object]
    compat: dict[str, object]
    features: dict[str, object]

    @property
    def runtime_version(self) -> str:
        """Return the Nanvix runtime version pinned by this SDK."""
        return cast(str, self.libc["nanvix_version"])

    @property
    def revision(self) -> int:
        """Return the numeric SDK revision."""
        match = _SDK_VERSION_RE.fullmatch(self.sdk_version)
        assert match is not None
        return int(match.group("revision"))

    def to_dict(self) -> dict[str, object]:
        """Return the canonical release contract as a JSON-compatible object."""
        return {
            "schema_version": self.schema_version,
            "sdk_version": self.sdk_version,
            "provider_id": self.provider_id,
            "provider": self.provider,
            "role": self.role,
            "image": {
                "name": self.image.name,
                "digest": self.image.digest,
                "ref": self.image.ref,
            },
            "target": dict(self.target),
            "toolchain": dict(self.toolchain),
            "libc": dict(self.libc),
            "compat": dict(self.compat),
            "features": dict(self.features),
        }

    def provenance(self) -> SdkProvenance:
        """Return the lockfile provenance represented by this contract."""
        return SdkProvenance(
            sdk_version=self.sdk_version,
            provider_id=self.provider_id,
            provider=self.provider,
            role=self.role,
            image=self.image,
            nanvix_tag=cast(str, self.libc["nanvix_tag"]),
            nanvix_version=self.runtime_version,
            nanvix_commit=cast(str, self.libc["nanvix_commit"]),
            sysroot_sha256=cast(str, self.libc["sysroot_sha256"]),
            compat=dict(self.compat),
            schema_version=self.schema_version,
            target=dict(self.target),
            toolchain=dict(self.toolchain),
            features=dict(self.features),
        )


def parse_sdk_version(version: str) -> tuple[str, int]:
    """Parse ``v<RUNTIME>-sdk.<REVISION>`` into runtime and revision."""
    match = _SDK_VERSION_RE.fullmatch(version)
    if match is None:
        raise SdkValidationError(
            f"invalid SDK version {version!r}; expected v<RUNTIME>-sdk.<REVISION>"
        )
    return match.group("runtime"), int(match.group("revision"))


def sdk_consumer_release_tag(package_version: str, sdk_version: str) -> str:
    """Build an exact SDK-aware consumer dependency release tag."""
    if not package_version or any(ch.isspace() for ch in package_version):
        raise SdkValidationError(
            "package version must be non-empty and whitespace-free"
        )
    runtime, revision = parse_sdk_version(sdk_version)
    return f"{package_version}-nanvix-{runtime}-sdk.{revision}"


def consumer_release_tag(
    package_version: str,
    nanvix_version: str,
    sdk_version: str | None = None,
) -> str:
    """Build a legacy or SDK-aware consumer dependency release tag."""
    if sdk_version is None:
        return f"{package_version}-nanvix-{nanvix_version.removeprefix('v')}"
    runtime, _revision = parse_sdk_version(sdk_version)
    if runtime != nanvix_version.removeprefix("v"):
        raise SdkValidationError(
            f"SDK runtime {runtime} does not match Nanvix {nanvix_version}"
        )
    return sdk_consumer_release_tag(package_version, sdk_version)


def _object(value: object, path: str) -> dict[str, object]:
    """Return *value* as an object or raise a path-specific validation error."""
    if not isinstance(value, dict):
        raise SdkValidationError(f"{path} must be an object")
    return cast(dict[str, object], value)


def _string(
    value: object,
    path: str,
    *,
    pattern: re.Pattern[str] | None = None,
) -> str:
    """Return a non-empty string after optional pattern validation."""
    if not isinstance(value, str) or not value:
        raise SdkValidationError(f"{path} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise SdkValidationError(f"{path} has invalid value {value!r}")
    return value


def validate_sdk_release(data: object) -> SdkRelease:
    """Validate and parse a schema-version-1 SDK release contract."""
    root = _object(data, "sdk release")
    missing = sorted(_REQUIRED_TOP_LEVEL - root.keys())
    if missing:
        raise SdkValidationError(
            f"sdk release is missing required keys: {', '.join(missing)}"
        )
    extra = sorted(root.keys() - _REQUIRED_TOP_LEVEL)
    if extra:
        raise SdkValidationError(f"sdk release has unexpected keys: {', '.join(extra)}")
    if (
        not isinstance(root["schema_version"], int)
        or isinstance(root["schema_version"], bool)
        or root["schema_version"] != 1
    ):
        raise SdkValidationError("schema_version must be 1")

    sdk_version = _string(root["sdk_version"], "sdk_version")
    runtime_from_version, _revision = parse_sdk_version(sdk_version)
    provider_id = _string(root["provider_id"], "provider_id")
    provider = _string(root["provider"], "provider")
    role = _string(root["role"], "role")
    if provider_id != "c-clang" or provider != "clang" or role != "c":
        raise SdkValidationError(
            "unsupported SDK provider tuple; expected c-clang/clang/c"
        )

    image_data = _object(root["image"], "image")
    if set(image_data) != {"name", "digest", "ref"}:
        raise SdkValidationError("image must contain exactly name, digest, and ref")
    image = SdkImage(
        name=_string(image_data["name"], "image.name"),
        digest=_string(image_data["digest"], "image.digest", pattern=_DIGEST_RE),
        ref=_string(image_data["ref"], "image.ref"),
    )
    if image.ref != f"{image.name}@{image.digest}":
        raise SdkValidationError(
            "image.ref must be the immutable '<image.name>@<image.digest>' coordinate"
        )
    expected_image = f"ghcr.io/nanvix/nanvix-sdk-{provider_id}"
    if image.name != expected_image:
        raise SdkValidationError(
            f"image.name must be {expected_image!r} for provider {provider_id!r}"
        )

    target = _object(root["target"], "target")
    toolchain = _object(root["toolchain"], "toolchain")
    libc = _object(root["libc"], "libc")
    compat = _object(root["compat"], "compat")
    features = _object(root["features"], "features")
    expected_nested_keys = {
        "target": {"triple", "alias"},
        "toolchain": {"llvm_version", "llvm_commit", "port_branch"},
        "libc": {
            "nanvix_tag",
            "nanvix_version",
            "nanvix_commit",
            "sysroot_sha256",
        },
        "compat": {"c_abi", "cxx_abi", "abi", "min_nanvix_os"},
        "features": {
            "localization",
            "filesystem",
            "wide_chars",
            "compiler_rt",
            "dynamic_loader",
        },
    }
    for name, value in (
        ("target", target),
        ("toolchain", toolchain),
        ("libc", libc),
        ("compat", compat),
        ("features", features),
    ):
        expected_keys = expected_nested_keys[name]
        if set(value) != expected_keys:
            missing_keys = sorted(expected_keys - value.keys())
            extra_keys = sorted(value.keys() - expected_keys)
            details = [
                *(f"missing {key}" for key in missing_keys),
                *(f"unexpected {key}" for key in extra_keys),
            ]
            raise SdkValidationError(
                f"{name} does not match schema: {', '.join(details)}"
            )
    for key in ("triple", "alias"):
        _string(target.get(key), f"target.{key}")
    if target["triple"] != "i686-unknown-nanvix":
        raise SdkValidationError("target.triple must be 'i686-unknown-nanvix'")
    if target["alias"] != "i686-nanvix":
        raise SdkValidationError("target.alias must be 'i686-nanvix'")

    for key in ("llvm_version", "llvm_commit", "port_branch"):
        _string(toolchain.get(key), f"toolchain.{key}")
    _string(toolchain["llvm_commit"], "toolchain.llvm_commit", pattern=_COMMIT_RE)

    for key in (
        "nanvix_tag",
        "nanvix_version",
        "nanvix_commit",
        "sysroot_sha256",
    ):
        _string(libc.get(key), f"libc.{key}")
    nanvix_tag = cast(str, libc["nanvix_tag"])
    nanvix_version = cast(str, libc["nanvix_version"])
    if nanvix_tag != f"v{nanvix_version}":
        raise SdkValidationError("libc.nanvix_tag and libc.nanvix_version disagree")
    if nanvix_version != runtime_from_version:
        raise SdkValidationError("sdk_version and libc.nanvix_version disagree")
    _string(libc["nanvix_commit"], "libc.nanvix_commit", pattern=_COMMIT_RE)
    sysroot_digest = cast(str, libc["sysroot_sha256"])
    if (
        _DIGEST_RE.fullmatch(sysroot_digest) is None
        and re.fullmatch(r"[0-9a-f]{64}", sysroot_digest) is None
    ):
        raise SdkValidationError("libc.sysroot_sha256 must be a SHA-256 digest")

    for key in ("c_abi", "cxx_abi", "abi", "min_nanvix_os"):
        _string(compat.get(key), f"compat.{key}")
    for key in ("localization", "filesystem", "wide_chars", "dynamic_loader"):
        if not isinstance(features.get(key), bool):
            raise SdkValidationError(f"features.{key} must be a boolean")
    compiler_rt = features.get("compiler_rt")
    if compiler_rt not in {"builtins-only", "full"}:
        raise SdkValidationError(
            "features.compiler_rt must be 'builtins-only' or 'full'"
        )
    min_os = cast(str, compat["min_nanvix_os"]).removeprefix("v")
    try:
        min_parts = tuple(int(part) for part in min_os.split("."))
        runtime_parts = tuple(int(part) for part in nanvix_version.split("."))
    except ValueError as exc:
        raise SdkValidationError(
            "compat.min_nanvix_os must be a semantic version"
        ) from exc
    if len(min_parts) != 3 or min_parts > runtime_parts:
        raise SdkValidationError("compat.min_nanvix_os exceeds the pinned runtime")

    return SdkRelease(
        schema_version=1,
        sdk_version=sdk_version,
        provider_id=provider_id,
        provider=provider,
        role=role,
        image=image,
        target=dict(target),
        toolchain=dict(toolchain),
        libc=dict(libc),
        compat=dict(compat),
        features=dict(features),
    )


def _artifact_contract(contract: SdkRelease) -> dict[str, object]:
    """Return fields that can be embedded before an image digest exists."""
    result = contract.to_dict()
    del result["image"]
    # These release-level coordinates are derived from the provider image and
    # SDK tag for schema-1 images published before the completion asset existed.
    # Newer images may carry them too; verification normalizes both forms.
    del result["provider_id"]
    libc = _object(result["libc"], "libc")
    libc.pop("nanvix_version", None)
    return result


def _normalize_embedded(
    value: object,
    path: str,
    contract: SdkRelease,
) -> dict[str, object]:
    """Normalize release-derived fields absent from schema-1 image manifests."""
    source = _object(value, path)
    result = dict(source)
    raw_image = result.pop("image", None)
    if raw_image is not None and raw_image != contract.to_dict()["image"]:
        raise SdkValidationError(f"{path} image coordinate conflicts with release")
    raw_provider_id = result.pop("provider_id", None)
    if raw_provider_id is not None and raw_provider_id != contract.provider_id:
        raise SdkValidationError(f"{path} provider_id conflicts with release")
    raw_libc = result.get("libc")
    if isinstance(raw_libc, dict):
        libc = dict(cast("dict[str, object]", raw_libc))
        raw_version = libc.pop("nanvix_version", None)
        if raw_version is not None and raw_version != contract.runtime_version:
            raise SdkValidationError(
                f"{path} libc.nanvix_version conflicts with release"
            )
        result["libc"] = libc
    return result


def verify_sdk_metadata(
    contract: SdkRelease,
    embedded: object,
    labels: object,
    docker_digest: str,
) -> None:
    """Verify release, embedded manifest, OCI labels, and Docker digest agree."""
    expected = _artifact_contract(contract)
    comparable = _normalize_embedded(
        embedded,
        "embedded SDK manifest",
        contract,
    )
    if comparable != expected:
        raise SdkValidationError(
            "embedded /opt/nanvix/nanvix-sdk.json does not match sdk-release.json"
        )

    label_obj = _object(labels, "OCI labels")
    manifest_label = label_obj.get(f"{SDK_LABEL_PREFIX}manifest")
    if isinstance(manifest_label, str):
        try:
            label_manifest: object = json.loads(manifest_label)
        except json.JSONDecodeError as exc:
            raise SdkValidationError(
                "OCI SDK manifest label is malformed JSON"
            ) from exc
        if (
            _normalize_embedded(
                label_manifest,
                "OCI SDK manifest label",
                contract,
            )
            != expected
        ):
            raise SdkValidationError("OCI SDK manifest label does not match release")
    else:
        missing: list[str] = []
        mismatched: list[str] = []

        def compare(prefix: str, value: object) -> None:
            if isinstance(value, dict):
                for key, child in cast(dict[str, object], value).items():
                    compare(f"{prefix}.{key}" if prefix else key, child)
                return
            key = f"{SDK_LABEL_PREFIX}{prefix}"
            actual = label_obj.get(key)
            rendered = (
                "true" if value is True else "false" if value is False else str(value)
            )
            if actual is None:
                # schema-1 images compute the staged sysroot digest during the
                # build, after static OCI labels are assembled. The value is
                # authoritative in the embedded manifest and release contract.
                if prefix != "libc.sysroot_sha256":
                    missing.append(key)
            elif actual != rendered:
                mismatched.append(key)

        compare("", expected)
        optional_labels = {
            f"{SDK_LABEL_PREFIX}provider_id": contract.provider_id,
            f"{SDK_LABEL_PREFIX}libc.nanvix_version": contract.runtime_version,
            f"{SDK_LABEL_PREFIX}image.name": contract.image.name,
            f"{SDK_LABEL_PREFIX}image.digest": contract.image.digest,
            f"{SDK_LABEL_PREFIX}image.ref": contract.image.ref,
        }
        for key, expected_value in optional_labels.items():
            actual = label_obj.get(key)
            if actual is not None and actual != expected_value:
                mismatched.append(key)
        if missing or mismatched:
            details = [*(f"missing {key}" for key in missing)]
            details.extend(f"mismatched {key}" for key in mismatched)
            raise SdkValidationError(
                "OCI labels do not match release: " + ", ".join(details)
            )

    if docker_digest != contract.image.digest:
        raise SdkValidationError(
            f"Docker digest {docker_digest!r} does not match {contract.image.digest!r}"
        )


def _release_asset(release: dict[str, object]) -> tuple[str, str]:
    """Extract the unique SDK contract URL and digest from release metadata."""
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise SdkValidationError("GitHub SDK release has no assets array")
    matches: list[tuple[str, str]] = []
    for item in cast(list[object], raw_assets):
        if not isinstance(item, dict):
            continue
        asset = cast(dict[str, object], item)
        if asset.get("name") != SDK_RELEASE_ASSET:
            continue
        url = asset.get("browser_download_url")
        digest = asset.get("digest")
        if isinstance(url, str) and isinstance(digest, str):
            matches.append((url, digest))
    if len(matches) != 1:
        raise SdkValidationError(
            f"GitHub SDK release must contain exactly one {SDK_RELEASE_ASSET}"
        )
    url, digest = matches[0]
    if _DIGEST_RE.fullmatch(digest) is None:
        raise SdkValidationError("SDK release contract asset has no valid digest")
    return url, digest


def _fetch_contract(
    url: str,
    expected_digest: str,
    gh_token: str | None,
) -> object:
    """Fetch a release contract without writing it to disk."""
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "nanvix-zutil",
    }
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30.0) as response:
            data = response.read()
        actual_digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if actual_digest != expected_digest:
            raise SdkValidationError(
                "SDK release contract asset digest does not match GitHub metadata"
            )
        return json.loads(data)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SdkValidationError(
            f"failed to fetch SDK release contract: {exc}"
        ) from exc


def resolve_sdk_release(
    version: str = "latest",
    *,
    provider_id: str = "c-clang",
    gh_token: str | None = None,
    verify_image: bool = True,
) -> SdkRelease:
    """Resolve and verify an authoritative SDK GitHub Release asset."""
    try:
        release = github.resolve_release(SDK_REPOSITORY, version, gh_token=gh_token)
        asset_url, asset_digest = _release_asset(release)
        contract = validate_sdk_release(
            _fetch_contract(asset_url, asset_digest, gh_token)
        )
        release_tag = release.get("tag_name")
        if not isinstance(release_tag, str) or release_tag != contract.sdk_version:
            raise SdkValidationError("GitHub release tag and sdk_version disagree")
        if provider_id != contract.provider_id:
            raise SdkValidationError(
                f"requested provider {provider_id!r}, release provides {contract.provider_id!r}"
            )
        if version != "latest" and version != contract.sdk_version:
            raise SdkValidationError(
                f"requested SDK {version!r}, release provides {contract.sdk_version!r}"
            )
        if verify_image:
            verify_sdk_image(contract)
        return contract
    except SdkValidationError as exc:
        fatal(str(exc), code=EXIT_INVALID_ARGS)
    except SystemExit:
        raise
    except OSError as exc:
        fatal(f"failed to resolve SDK release: {exc}", code=EXIT_NETWORK_ERROR)


def verify_sdk_image(contract: SdkRelease, *, pull: bool = True) -> None:
    """Verify an SDK image's digest, embedded manifest, and OCI labels.

    Args:
        contract: Validated release contract.
        pull: Pull the immutable reference first. Offline callers may set this
            to ``False`` to verify an already-present image.
    """
    try:
        if pull:
            pull_result = subprocess.run(
                ["docker", "pull", contract.image.ref],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if pull_result.returncode != 0:
                fatal(
                    f"failed to pull verified SDK image {contract.image.ref}:"
                    f" {pull_result.stderr.strip()}",
                    code=EXIT_MISSING_DEP,
                )
        inspect = subprocess.run(
            ["docker", "image", "inspect", contract.image.ref],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        embedded = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                contract.image.ref,
                "cat",
                "/opt/nanvix/nanvix-sdk.json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        fatal(f"Docker SDK verification failed: {exc}", code=EXIT_MISSING_DEP)
    if inspect.returncode != 0 or embedded.returncode != 0:
        fatal("Docker SDK metadata inspection failed", code=EXIT_MISSING_DEP)
    try:
        inspected: object = json.loads(inspect.stdout)
        embedded_data: object = json.loads(embedded.stdout)
        if not isinstance(inspected, list):
            raise SdkValidationError(
                "docker image inspect returned an unexpected result"
            )
        inspected_items = cast("list[object]", inspected)
        if len(inspected_items) != 1:
            raise SdkValidationError(
                "docker image inspect returned an unexpected result"
            )
        image_data = _object(inspected_items[0], "docker image inspect")
        config = _object(image_data.get("Config"), "docker image Config")
        labels = config.get("Labels")
        repo_digests = image_data.get("RepoDigests")
        if not isinstance(repo_digests, list):
            raise SdkValidationError("docker image inspect has no RepoDigests")
        expected_ref = contract.image.ref
        if expected_ref not in repo_digests:
            raise SdkValidationError(
                "Docker RepoDigests does not contain the release ref"
            )
        verify_sdk_metadata(
            contract,
            embedded_data,
            labels,
            expected_ref.rpartition("@")[2],
        )
    except (json.JSONDecodeError, SdkValidationError) as exc:
        fatal(f"SDK image verification failed: {exc}", code=EXIT_INVALID_ARGS)
