# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""BFS dependency resolver for the Nanvix lockfile.

Resolves a :class:`~nanvix_zutil.manifest.Manifest` into a fully pinned
:class:`~nanvix_zutil.lockfile.Lockfile` by walking the dependency graph.
Transitive dependencies are discovered by downloading the shallow
``nanvix.lock`` release asset shipped alongside each dependency's
per-scope archives.
"""

from __future__ import annotations

import shutil
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from nanvix_zutil import github, log
from nanvix_zutil.buildroot import Dependency, RefKind
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedAsset,
    ResolvedPackage,
    compute_manifest_hash,
    compute_manifest_hash_bytes,
    download_lockfile_asset,
    get_zutil_version,
)
from nanvix_zutil.manifest import Manifest, load_manifest
from nanvix_zutil.paths import manifest_path, nanvix_root
from nanvix_zutil.sdk import SdkRelease, resolve_sdk_release

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _QueueItem:
    """An item in the BFS resolution queue.

    Attributes:
        dep: The dependency to resolve.
        transitive: Whether this dependency was discovered transitively.
        required_by: Name of the package that pulled this dependency in.
    """

    dep: Dependency
    transitive: bool = False
    required_by: str = ""


@dataclass(frozen=True)
class BlockedResolution:
    """Machine-readable exact SDK resolution failure."""

    status: str
    package: str
    repo: str
    requested_tag: str
    sdk_version: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible blocked result."""
        return {
            "status": self.status,
            "package": self.package,
            "repo": self.repo,
            "requested_tag": self.requested_tag,
            "sdk_version": self.sdk_version,
            "reason": self.reason,
        }


_ARCHIVE_EXTENSIONS = (".tar.bz2", ".tar.gz", ".zip")


def _collect_assets(release: dict[str, object]) -> list[ResolvedAsset]:
    """Extract archive assets from a GitHub release.

    Keeps ``.tar.bz2``, ``.tar.gz``, and ``.zip`` archives.  Filters out
    ``nanvix.lock`` and any non-archive files.

    Args:
        release: GitHub release metadata dictionary.

    Returns:
        List of :class:`ResolvedAsset` for archive files.
    """
    raw_assets: object = release.get("assets", [])
    if not isinstance(raw_assets, list):
        return []

    result: list[ResolvedAsset] = []
    for item in cast("list[object]", raw_assets):
        if not isinstance(item, dict):
            continue
        asset = cast("dict[str, object]", item)
        name = asset.get("name")
        url = asset.get("browser_download_url")
        asset_id = asset.get("id")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if asset_id is not None and not isinstance(asset_id, int):
            continue
        if not any(name.endswith(ext) for ext in _ARCHIVE_EXTENSIONS):
            continue
        result.append(ResolvedAsset(name=name, url=url, asset_id=asset_id))
    return result


def _extract_release_fields(
    release: dict[str, object],
) -> tuple[str, str, int]:
    """Extract tag_name, target_commitish, and id from a release dict.

    Logs a warning if any field is missing or has an unexpected type.

    Args:
        release: GitHub release metadata dictionary.

    Returns:
        Tuple of (tag_name, target_commitish, release_id).
    """
    tag_name = release.get("tag_name", "")
    if not isinstance(tag_name, str):
        log.warning(f"Release missing 'tag_name' (got {type(tag_name).__name__})")
        tag_name = ""
    commitish = release.get("target_commitish", "")
    if not isinstance(commitish, str):
        log.warning(
            f"Release missing 'target_commitish' (got {type(commitish).__name__})"
        )
        commitish = ""
    release_id = release.get("id", 0)
    if not isinstance(release_id, int):
        log.warning(f"Release missing 'id' (got {type(release_id).__name__})")
        release_id = 0
    return tag_name, commitish, release_id


def _detect_cycles(packages: list[ResolvedPackage]) -> None:
    """Detect cycles in the dependency graph.

    Builds an adjacency list from package dependencies and runs a DFS.
    Calls :func:`log.fatal` with ``EXIT_INVALID_ARGS`` if a cycle is
    found.

    Args:
        packages: List of resolved packages to check.
    """
    adjacency: dict[str, list[str]] = {}
    for pkg in packages:
        adjacency[pkg.name] = list(pkg.dependencies)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in adjacency}
    path: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                log.fatal(
                    f"Dependency cycle detected: {' → '.join(cycle)}",
                    code=EXIT_INVALID_ARGS,
                    hint="Remove or break the circular dependency.",
                )
            if color[neighbor] == WHITE:
                dfs(neighbor)
        path.pop()
        color[node] = BLACK

    for node in adjacency:
        if color[node] == WHITE:
            dfs(node)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve(
    manifest: Manifest,
    gh_token: str | None = None,
    cache_dir: Path | None = None,
    *,
    shallow: bool = False,
    verified_sdk_release: SdkRelease | None = None,
    verify_sdk_image: bool = True,
    manifest_content: bytes | None = None,
) -> Lockfile | BlockedResolution:
    """Resolve a manifest into a fully pinned lockfile.

    Implements BFS over the dependency graph:

    1. Resolve the sysroot via ``github.resolve_release()`` with
       ``semver=True``.
    2. Seed the queue with direct dependencies from the manifest.
    3. For each dependency, resolve its release, then (unless *shallow*)
       download its ``nanvix.lock`` release asset to discover transitive
       dependencies.
    4. Detect cycles in the resolved graph.
    5. Collect ``.tar.bz2`` assets for every resolved package.
    6. Compute the manifest hash and assemble the :class:`Lockfile`.

    Args:
        manifest: Parsed manifest from :func:`load_manifest`.
        gh_token: Optional GitHub personal access token.
        cache_dir: Directory for temporary lockfile downloads. Defaults to a
            confined per-process directory under ``.nanvix/cache``.
        shallow: When ``True``, skip transitive dependency discovery.
            Resolves only the sysroot and direct dependencies.
        verified_sdk_release: Already verified SDK contract. Supplying this
            avoids redundant Docker verification.
        verify_sdk_image: Verify the selected SDK image when resolving the
            contract. Enabled by default.
        manifest_content: Candidate manifest bytes used to hash an atomic
            update before the target file is written.

    Returns:
        The fully resolved :class:`Lockfile`.

    Raises:
        SystemExit: On cycle detection, version conflicts, or network
            errors.
    """
    tmp_dir = (
        Path(cache_dir)
        if cache_dir
        else nanvix_root() / "cache" / f"resolve-{os.getpid()}"
    )
    owns_tmp = cache_dir is None
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        return _resolve_inner(
            manifest,
            gh_token,
            tmp_dir,
            shallow=shallow,
            verified_sdk_release=verified_sdk_release,
            verify_sdk_image=verify_sdk_image,
            manifest_content=manifest_content,
        )
    finally:
        if owns_tmp and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _resolve_inner(
    manifest: Manifest,
    gh_token: str | None,
    cache_dir: Path,
    *,
    shallow: bool,
    verified_sdk_release: SdkRelease | None,
    verify_sdk_image: bool,
    manifest_content: bytes | None,
) -> Lockfile | BlockedResolution:
    """Inner resolver logic (separated for cleanup in ``resolve()``)."""
    resolved: dict[str, ResolvedPackage] = {}
    releases: dict[str, dict[str, object]] = {}

    # 1. Resolve sysroot
    sdk_pin = manifest.toolchain.sdk
    sdk_release = verified_sdk_release or resolve_sdk_release(
        sdk_pin.version,
        provider_id=sdk_pin.provider_id,
        gh_token=gh_token,
        verify_image=verify_sdk_image,
    )
    if (
        sdk_release.sdk_version != sdk_pin.version
        or sdk_release.provider_id != sdk_pin.provider_id
    ):
        log.fatal(
            "Trusted SDK release does not match the manifest coordinate",
            code=EXIT_INVALID_ARGS,
        )
    if (
        sdk_release.image.name != sdk_pin.image_name
        or sdk_release.image.digest != sdk_pin.digest
        or sdk_release.image.ref != sdk_pin.image_ref
    ):
        log.fatal(
            "Manifest SDK image pin does not match the verified SDK release",
            code=EXIT_INVALID_ARGS,
        )
    sysroot_specifier = cast(str, sdk_release.libc["nanvix_tag"])
    sdk_provenance = sdk_release.provenance()
    try:
        sysroot_release = github.resolve_release(
            "nanvix/nanvix",
            sysroot_specifier,
            gh_token=gh_token,
            semver=True,
        )
    except SystemExit:
        log.note(
            "while resolving the Nanvix sysroot" f" (nanvix/nanvix@{sysroot_specifier})"
        )
        raise
    tag, commitish, rel_id = _extract_release_fields(sysroot_release)
    expected_tag = cast(str, sdk_release.libc["nanvix_tag"])
    expected_commit = cast(str, sdk_release.libc["nanvix_commit"])
    if tag != expected_tag:
        log.fatal(
            f"SDK runtime tag skew: expected {expected_tag}, resolved {tag}",
            code=EXIT_INVALID_ARGS,
        )
    resolved_runtime_commit = github.resolve_commit(
        "nanvix/nanvix",
        expected_tag,
        gh_token=gh_token,
    )
    if resolved_runtime_commit != expected_commit:
        log.fatal(
            "SDK runtime commit does not match the Nanvix release",
            code=EXIT_INVALID_ARGS,
        )
    # The ref preserves the runtime semver while resolved_tag is the canonical
    # pin used for deterministic artifact downloads.
    sysroot_pkg = ResolvedPackage(
        name="nanvix",
        repo="nanvix/nanvix",
        kind="sysroot",
        ref=manifest.sysroot_ref,
        resolved_tag=tag,
        resolved_commitish=commitish,
        release_id=rel_id,
    )
    resolved["nanvix"] = sysroot_pkg
    releases["nanvix"] = sysroot_release

    # 2. Seed queue with direct deps
    queue: deque[_QueueItem] = deque()
    all_deps = list(manifest.dependencies) + list(manifest.system_dependencies)
    for dep in all_deps:
        if (
            dep.ref.kind != RefKind.VERSION
            or not isinstance(dep.ref.value, str)
            or not dep.ref.value.endswith(
                f"-nanvix-{sdk_release.sdk_version.removeprefix('v')}"
            )
        ):
            log.fatal(
                f"SDK dependency '{dep.name}' must use a version"
                " specifier so its exact SDK-aware release tag is derived",
                code=EXIT_INVALID_ARGS,
            )
    for dep in all_deps:
        queue.append(_QueueItem(dep=dep))

    # 3. BFS
    while queue:
        item = queue.popleft()
        dep = item.dep
        expected_suffix = f"-nanvix-{sdk_release.sdk_version.removeprefix('v')}"
        if (
            dep.ref.kind != RefKind.VERSION
            or not isinstance(dep.ref.value, str)
            or not dep.ref.value.endswith(expected_suffix)
        ):
            log.fatal(
                f"SDK dependency '{dep.name}' does not use exact"
                f" SDK-aware coordinate '*{expected_suffix}'",
                code=EXIT_INVALID_ARGS,
            )

        if dep.name in resolved:
            # Version conflict detection: same name, different release
            existing = resolved[dep.name]
            if (existing.ref.kind, existing.ref.value) != (dep.ref.kind, dep.ref.value):
                requesters = [item.required_by] if item.required_by else ["manifest"]
                existing_requesters = (
                    existing.required_by if existing.required_by else ["manifest"]
                )
                log.fatal(
                    f"Version conflict for '{dep.name}': "
                    f"{', '.join(existing_requesters)} require "
                    f"'{existing.ref.value}' but "
                    f"{', '.join(requesters)} require '{dep.ref.value}'",
                    code=EXIT_INVALID_ARGS,
                    hint="Resolve the conflict by pinning a single version.",
                )
            # Track additional requesters for deduped deps
            if item.required_by and item.required_by not in existing.required_by:
                existing.required_by.append(item.required_by)
            continue

        try:
            dep_release = github.resolve_release(
                dep.repo,
                dep.ref.value,
                gh_token=gh_token,
            )
        except SystemExit as exc:
            requester = item.required_by or "manifest"
            if exc.code == 3:
                return BlockedResolution(
                    status="blocked",
                    package=dep.name,
                    repo=dep.repo,
                    requested_tag=str(dep.ref.value),
                    sdk_version=sdk_release.sdk_version,
                    reason="exact-sdk-release-missing",
                )
            log.note(
                f"while resolving dependency '{dep.name}'"
                f" ({dep.repo}@{dep.ref.value}),"
                f" required by {requester}"
            )
            raise
        d_tag, d_commitish, d_rel_id = _extract_release_fields(dep_release)

        pkg = ResolvedPackage(
            name=dep.name,
            repo=dep.repo,
            kind="dependency",
            ref=dep.ref,
            resolved_tag=d_tag,
            resolved_commitish=d_commitish,
            release_id=d_rel_id,
            transitive=item.transitive,
            required_by=[item.required_by] if item.required_by else [],
        )
        resolved[dep.name] = pkg
        releases[dep.name] = dep_release

        # SDK resolution always verifies the dependency's shallow lock
        # provenance, even when this resolver invocation is itself shallow.
        inner_lock = download_lockfile_asset(
            dep_release, cache_dir, gh_token=gh_token, dep_name=dep.name
        )
        if inner_lock is None:
            return BlockedResolution(
                status="blocked",
                package=dep.name,
                repo=dep.repo,
                requested_tag=str(dep.ref.value),
                sdk_version=sdk_release.sdk_version,
                reason="provenance-lock-missing",
            )
        if inner_lock.metadata.sdk != sdk_provenance:
            log.fatal(
                f"Dependency '{dep.name}' lock provenance does not match"
                " the selected SDK",
                code=EXIT_INVALID_ARGS,
            )
        if shallow:
            continue
        for trans_pkg in inner_lock.packages:
            if trans_pkg.kind == "sysroot":
                continue
            if trans_pkg.name not in resolved:
                pkg.dependencies.append(trans_pkg.name)
                queue.append(
                    _QueueItem(
                        dep=Dependency(
                            name=trans_pkg.name,
                            repo=trans_pkg.repo,
                            ref=trans_pkg.ref,
                        ),
                        transitive=True,
                        required_by=dep.name,
                    )
                )
            else:
                # Already resolved — check for version conflicts
                existing = resolved[trans_pkg.name]
                if (existing.ref.kind, existing.ref.value) != (
                    trans_pkg.ref.kind,
                    trans_pkg.ref.value,
                ):
                    existing_requesters = (
                        existing.required_by if existing.required_by else ["manifest"]
                    )
                    log.fatal(
                        f"Version conflict for '{trans_pkg.name}': "
                        f"{', '.join(existing_requesters)} require "
                        f"'{existing.ref.value}' but "
                        f"{dep.name} requires "
                        f"'{trans_pkg.ref.value}'",
                        code=EXIT_INVALID_ARGS,
                        hint="Resolve the conflict by pinning a single version.",
                    )
                # Track additional requester
                if dep.name not in existing.required_by:
                    existing.required_by.append(dep.name)
                pkg.dependencies.append(trans_pkg.name)

    # 4. Detect cycles
    _detect_cycles(list(resolved.values()))

    # 5. Collect assets
    for name, pkg in resolved.items():
        pkg.assets = _collect_assets(releases[name])

    # 6. Assemble lockfile
    manifest_hash = (
        compute_manifest_hash_bytes(manifest_content)
        if manifest_content is not None
        else compute_manifest_hash(manifest_path())
    )

    metadata = LockfileMetadata(
        manifest_hash=manifest_hash,
        nanvix_zutil_version=get_zutil_version(),
        sdk=sdk_provenance,
        shallow=shallow,
    )

    return Lockfile(metadata=metadata, packages=list(resolved.values()))


def is_stale(lockfile: Lockfile, *, shallow: bool | None = None) -> bool:
    """Check whether a lockfile is stale relative to its manifest.

    Compares the ``manifest_hash`` stored in the lockfile metadata
    against the current hash of the manifest file.

    Args:
        lockfile: The lockfile to check.
        shallow: Expected shallow-lock mode. When supplied, a lock generated
            in the other mode is stale.

    Returns:
        ``True`` if the lockfile is stale (hashes differ).
    """
    if shallow is not None and lockfile.metadata.shallow != shallow:
        return True
    current_hash = compute_manifest_hash(manifest_path())
    if lockfile.metadata.manifest_hash != current_hash:
        return True
    manifest = load_manifest()
    sdk_pin = manifest.toolchain.sdk
    provenance = lockfile.metadata.sdk
    return (
        provenance.sdk_version != sdk_pin.version
        or provenance.provider_id != sdk_pin.provider_id
        or provenance.image.ref != sdk_pin.image_ref
    )
