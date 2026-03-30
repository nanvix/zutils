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
- :func:`~nanvix_zutil.docker.docker_available` — check Docker CLI presence
- :func:`~nanvix_zutil.docker.image_exists` — check local image availability
- :class:`~nanvix_zutil.info.NanvixInfo` — resolved Nanvix release information
- :func:`~nanvix_zutil.info.get_nanvix_info` — query Nanvix release info
- ``BUILDROOT_CONTAINER_PATH``, ``SYSROOT_CONTAINER_PATH``, ``WORKSPACE_CONTAINER_PATH``, ``TOOLCHAIN_CONTAINER_PATH`` — well-known container paths
- :mod:`nanvix_zutil.log` — structured logging helpers
"""

from nanvix_zutil.buildroot import Buildroot, Dependency, Ref, RefKind, suffix_dep
from nanvix_zutil.config import (
    CFG_GH_TOKEN,
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    Config,
)
from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
    DEFAULT_DOCKER_IMAGE,
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    docker_available,
    image_exists,
)
from nanvix_zutil.exitcodes import (
    EXIT_BUILD_FAILURE,
    EXIT_GENERAL_ERROR,
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
    EXIT_NETWORK_ERROR,
    EXIT_SUCCESS,
    EXIT_TEST_FAILURE,
)
from nanvix_zutil.github import resolve_release
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
from nanvix_zutil.resolver import is_stale, resolve
from nanvix_zutil.script import ZScript
from nanvix_zutil.sysroot import Sysroot

__all__ = [
    "Buildroot",
    "BUILDROOT_CONTAINER_PATH",
    "CFG_GH_TOKEN",
    "CFG_SYSROOT",
    "CFG_TOOLCHAIN",
    "Config",
    "DEFAULT_DOCKER_IMAGE",
    "Dependency",
    "DockerConfig",
    "EXIT_BUILD_FAILURE",
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
    "docker_available",
    "get_nanvix_info",
    "image_exists",
    "is_stale",
    "load_manifest",
    "read_lockfile",
    "resolve",
    "resolve_release",
    "suffix_dep",
    "write_lockfile",
]
