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
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_MACHINE,
    DEFAULT_MEMORY_SIZE,
    DEFAULT_TARGET,
    Config,
)
from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    is_windows,
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
from nanvix_zutil.manifest import Manifest, load_manifest
from nanvix_zutil.release import DEFAULT_FORMATS, ArchiveFormat, package
from nanvix_zutil.resolver import is_stale, resolve
from nanvix_zutil.script import ZScript
from nanvix_zutil.sysroot import Sysroot

__all__ = [
    "ArchiveFormat",
    "Buildroot",
    "BUILDROOT_CONTAINER_PATH",
    "CFG_DOCKER_IMAGE",
    "CFG_GH_TOKEN",
    "CFG_SYSROOT",
    "Config",
    "DEFAULT_DEPLOYMENT_MODE",
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
    "extract_nanvix_version",
    "extract_nanvix_version_base",
    "get_nanvix_info",
    "is_stale",
    "load_manifest",
    "package",
    "parse_semver_tuple",
    "read_lockfile",
    "resolve",
    "resolve_release",
    "resolve_release_with_fallback",
    "suffix_dep",
    "write_lockfile",
]
