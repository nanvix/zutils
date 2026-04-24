# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""nanvix_zutil — Build orchestration utilities for the Nanvix ecosystem.

Public re-exports:

- :class:`~nanvix_zutil.script.ZScript` — base class for consumer build scripts
- :class:`~nanvix_zutil.config.Config` — persistent build configuration
- :class:`~nanvix_zutil.buildroot.Buildroot` — build-time dependency root
- :class:`~nanvix_zutil.buildroot.Dependency` — library dependency descriptor
- :class:`~nanvix_zutil.sysroot.Sysroot` — runtime sysroot management
- :class:`~nanvix_zutil.manifest.Manifest` — parsed TOML manifest
- :class:`~nanvix_zutil.manifest.BuildMatrix` — parsed ``[builds]`` section
- :func:`~nanvix_zutil.manifest.load_manifest` — parse nanvix.toml
- :class:`~nanvix_zutil.lockfile.Lockfile` — resolved dependency graph
- :class:`~nanvix_zutil.lockfile.ResolvedPackage` — a resolved dependency
- :class:`~nanvix_zutil.lockfile.ResolvedAsset` — a downloadable release artifact
- :class:`~nanvix_zutil.lockfile.LockfileMetadata` — lockfile header metadata
- :func:`~nanvix_zutil.lockfile.write_lockfile` — serialize lockfile to TOML
- :func:`~nanvix_zutil.lockfile.read_lockfile` — parse lockfile from TOML
- :func:`~nanvix_zutil.resolver.resolve` — resolve manifest to lockfile
- :func:`~nanvix_zutil.resolver.is_stale` — check lockfile staleness
- :class:`~nanvix_zutil.docker.DockerConfig` — Docker run configuration
- :class:`~nanvix_zutil.docker.Mount` — Docker volume mount descriptor
- :func:`~nanvix_zutil.docker.docker_available` — check Docker CLI presence
- :func:`~nanvix_zutil.docker.image_exists` — check local image availability
- :class:`~nanvix_zutil.info.NanvixInfo` — resolved Nanvix release information
- :func:`~nanvix_zutil.info.get_nanvix_info` — query Nanvix release info
- :func:`~nanvix_zutil.github.resolve_release` — resolve a release by version specifier
- :func:`~nanvix_zutil.github.resolve_release_with_fallback` — resolve a release with fallback to best available
- :func:`~nanvix_zutil.buildroot.suffix_dep` — suffix a dep's VERSION ref with a nanvix version
- :func:`~nanvix_zutil.buildroot.extract_nanvix_version` — extract nanvix version from a suffixed tag
- :func:`~nanvix_zutil.buildroot.extract_nanvix_version_base` — extract base version from a suffixed tag
- :func:`~nanvix_zutil.buildroot.parse_semver_tuple` — parse semver string to tuple for comparison
- :mod:`nanvix_zutil.log` — structured logging helpers
- :class:`~nanvix_zutil.release.ArchiveFormat` — supported archive formats
- :data:`~nanvix_zutil.release.DEFAULT_FORMATS` — default archive formats
- :func:`~nanvix_zutil.release.package` — create release archives
- :class:`~nanvix_zutil.matrix.BuildCombo` — a single build configuration
- :class:`~nanvix_zutil.matrix.BuildResult` — outcome of a build run
- :func:`~nanvix_zutil.matrix.expand_matrix` — expand a build matrix to all combos
- :func:`~nanvix_zutil.matrix.filter_matrix` — filter combos by mode
- :func:`~nanvix_zutil.matrix.run_all_builds` — run a hook across all combos in parallel
- :func:`~nanvix_zutil.docker.is_windows` — Windows platform detection helper
"""

from nanvix_zutil.buildroot import (
    Buildroot,
    Dependency,
    Ref,
    RefKind,
    extract_nanvix_version,
    extract_nanvix_version_base,
    parse_semver_tuple,
    suffix_dep,
)
from nanvix_zutil.config import (
    CFG_DOCKER_IMAGE,
    CFG_GH_TOKEN,
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    Config,
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_MACHINE,
    DEFAULT_MEMORY_SIZE,
    DEFAULT_TARGET,
)
from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
    DEFAULT_DOCKER_IMAGE,
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    is_windows,
    docker_available,
    image_exists,
)
from nanvix_zutil.exitcodes import (
    EXIT_BUILD_FAILURE,
    EXIT_DEGRADED_SETUP,
    EXIT_GENERAL_ERROR,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
    EXIT_NETWORK_ERROR,
    EXIT_SUCCESS,
    EXIT_TEST_FAILURE,
)
from nanvix_zutil.github import resolve_release, resolve_release_with_fallback
from nanvix_zutil.info import NanvixInfo, get_nanvix_info
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedAsset,
    ResolvedPackage,
    read_lockfile,
    write_lockfile,
)
from nanvix_zutil.manifest import BuildMatrix, Manifest, load_manifest
from nanvix_zutil.matrix import (
    BuildCombo,
    BuildResult,
    expand_matrix,
    filter_matrix,
    run_all_builds,
)
from nanvix_zutil.release import DEFAULT_FORMATS, ArchiveFormat, package
from nanvix_zutil.resolver import is_stale, resolve
from nanvix_zutil.script import ZScript
from nanvix_zutil.sysroot import Sysroot

__all__ = [
    "ArchiveFormat",
    "BuildCombo",
    "BuildMatrix",
    "BuildResult",
    "Buildroot",
    "BUILDROOT_CONTAINER_PATH",
    "CFG_DOCKER_IMAGE",
    "CFG_GH_TOKEN",
    "CFG_SYSROOT",
    "CFG_TOOLCHAIN",
    "Config",
    "DEFAULT_DEPLOYMENT_MODE",
    "DEFAULT_DOCKER_IMAGE",
    "DEFAULT_FORMATS",
    "DEFAULT_MACHINE",
    "DEFAULT_MEMORY_SIZE",
    "DEFAULT_TARGET",
    "Dependency",
    "DockerConfig",
    "EXIT_BUILD_FAILURE",
    "EXIT_DEGRADED_SETUP",
    "EXIT_GENERAL_ERROR",
    "EXIT_INVALID_ARGS",
    "EXIT_MISSING_DEP",
    "EXIT_NETWORK_ERROR",
    "EXIT_SUCCESS",
    "EXIT_TEST_FAILURE",
    "Lockfile",
    "LockfileMetadata",
    "Manifest",
    "Mount",
    "NanvixInfo",
    "Ref",
    "RefKind",
    "ResolvedAsset",
    "ResolvedPackage",
    "SYSROOT_CONTAINER_PATH",
    "TOOLCHAIN_CONTAINER_PATH",
    "WORKSPACE_CONTAINER_PATH",
    "Sysroot",
    "ZScript",
    "is_windows",
    "docker_available",
    "extract_nanvix_version",
    "extract_nanvix_version_base",
    "expand_matrix",
    "filter_matrix",
    "get_nanvix_info",
    "image_exists",
    "is_stale",
    "load_manifest",
    "package",
    "parse_semver_tuple",
    "read_lockfile",
    "resolve",
    "resolve_release",
    "resolve_release_with_fallback",
    "run_all_builds",
    "suffix_dep",
    "write_lockfile",
]
