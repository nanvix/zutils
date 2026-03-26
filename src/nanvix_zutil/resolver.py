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
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from nanvix_zutil import github, log
from nanvix_zutil.buildroot import Dependency, Ref, RefKind
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedAsset,
    ResolvedPackage,
    compute_manifest_hash,
    download_lockfile_asset,
    get_zutil_version,
)
from nanvix_zutil.manifest import Manifest

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


def _collect_assets(release: dict[str, object]) -> list[ResolvedAsset]:
    """Extract ``.tar.bz2`` assets from a GitHub release.

    Filters out ``nanvix.lock``, source archives (``*.tar.gz``,
    ``*.zip``), and any non-``.tar.bz2`` files.

    Args:
        release: GitHub release metadata dictionary.

    Returns:
        List of :class:`ResolvedAsset` for ``.tar.bz2`` files.
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
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if not name.endswith(".tar.bz2"):
            continue
        result.append(ResolvedAsset(name=name, url=url))
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
    manifest_path: Path | None = None,
) -> Lockfile:
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
        cache_dir: Directory for temporary lockfile downloads.  Defaults
            to a ``tempfile.mkdtemp()``; cleaned up on completion.
        shallow: When ``True``, skip transitive dependency discovery.
            Resolves only the sysroot and direct dependencies.
        manifest_path: Path to the ``nanvix.toml`` file for hash
            computation.  Defaults to ``.nanvix/nanvix.toml``.

    Returns:
        The fully resolved :class:`Lockfile`.

    Raises:
        SystemExit: On cycle detection, version conflicts, or network
            errors.
    """
    tmp_dir = Path(cache_dir) if cache_dir else Path(tempfile.mkdtemp())
    owns_tmp = cache_dir is None
    m_path = manifest_path or Path(".nanvix") / "nanvix.toml"

    try:
        return _resolve_inner(manifest, gh_token, tmp_dir, m_path, shallow=shallow)
    finally:
        if owns_tmp and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _resolve_inner(
    manifest: Manifest,
    gh_token: str | None,
    cache_dir: Path,
    manifest_path: Path,
    *,
    shallow: bool,
) -> Lockfile:
    """Inner resolver logic (separated for cleanup in ``resolve()``)."""
    resolved: dict[str, ResolvedPackage] = {}
    releases: dict[str, dict[str, object]] = {}

    # 1. Resolve sysroot
    sysroot_release = github.resolve_release(
        "nanvix/nanvix",
        manifest.sysroot_ref.value,
        gh_token=gh_token,
        semver=True,
    )
    tag, commitish, rel_id = _extract_release_fields(sysroot_release)
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

    # Deferred auto-suffix: when sysroot is "latest", load_manifest()
    # skips suffixing because the real version isn't known yet.  Now
    # that the sysroot is resolved, extract the version from its tag
    # and apply the suffix to VERSION deps.
    if manifest.sysroot_ref.value == "latest":
        resolved_version = tag.removeprefix("v")
        for dep in [*manifest.dependencies, *manifest.system_dependencies]:
            if dep.ref.kind == RefKind.VERSION and isinstance(dep.ref.value, str):
                dep.ref = Ref(
                    kind=dep.ref.kind,
                    value=f"{dep.ref.value}-nanvix-{resolved_version}",
                )

    # 2. Seed queue with direct deps
    queue: deque[_QueueItem] = deque()
    all_deps = list(manifest.dependencies) + list(manifest.system_dependencies)
    for dep in all_deps:
        queue.append(_QueueItem(dep=dep))

    # 3. BFS
    while queue:
        item = queue.popleft()
        dep = item.dep

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

        dep_release = github.resolve_release(
            dep.repo,
            dep.ref.value,
            gh_token=gh_token,
        )
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

        # Discover transitive deps (unless shallow)
        if not shallow:
            inner_lock = download_lockfile_asset(
                dep_release, cache_dir, gh_token=gh_token, dep_name=dep.name
            )
            if inner_lock is not None:
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
                                existing.required_by
                                if existing.required_by
                                else ["manifest"]
                            )
                            log.fatal(
                                f"Version conflict for '{trans_pkg.name}': "
                                f"{', '.join(existing_requesters)} require "
                                f"'{existing.ref.value}' but "
                                f"{dep.name} requires "
                                f"'{trans_pkg.ref.value}'",
                                code=EXIT_INVALID_ARGS,
                                hint="Resolve the conflict by pinning "
                                "a single version.",
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
    manifest_hash = compute_manifest_hash(manifest_path)

    metadata = LockfileMetadata(
        manifest_hash=manifest_hash,
        nanvix_zutil_version=get_zutil_version(),
    )

    return Lockfile(metadata=metadata, packages=list(resolved.values()))


def is_stale(lockfile: Lockfile, manifest_path: Path) -> bool:
    """Check whether a lockfile is stale relative to its manifest.

    Compares the ``manifest_hash`` stored in the lockfile metadata
    against the current hash of the manifest file.

    Args:
        lockfile: The lockfile to check.
        manifest_path: Path to the ``nanvix.toml`` file.

    Returns:
        ``True`` if the lockfile is stale (hashes differ).
    """
    current_hash = compute_manifest_hash(manifest_path)
    return lockfile.metadata.manifest_hash != current_hash
