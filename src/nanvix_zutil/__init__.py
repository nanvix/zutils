# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""nanvix_zutil — Build orchestration utilities for the Nanvix ecosystem.

Public re-exports:

- :class:`~nanvix_zutil.script.ZScript` — base class for consumer build scripts
- :class:`~nanvix_zutil.config.Config` — persistent build configuration
- :class:`~nanvix_zutil.buildroot.Dependency` — library dependency descriptor
- :class:`~nanvix_zutil.sysroot.Sysroot` — runtime sysroot + build-time dependency management
- :class:`~nanvix_zutil.manifest.Manifest` — parsed TOML manifest
- :class:`~nanvix_zutil.manifest.Toolchain` — immutable SDK selection
- :class:`~nanvix_zutil.manifest.SdkPin` — immutable manifest SDK coordinate
- :class:`~nanvix_zutil.sdk.SdkRelease` — verified SDK release contract
- :class:`~nanvix_zutil.sdk.SdkProvenance` — lockfile SDK provenance
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
- :class:`~nanvix_zutil.commands.info.NanvixInfo` — resolved Nanvix release information
- :func:`~nanvix_zutil.commands.info.get_nanvix_info` — query Nanvix release info
- :func:`~nanvix_zutil.github.resolve_release` — resolve a release by version specifier
- :func:`~nanvix_zutil.buildroot.suffix_dep` — suffix a dep's VERSION ref with a nanvix version
- :func:`~nanvix_zutil.buildroot.extract_nanvix_version` — extract nanvix version from a suffixed tag
- :func:`~nanvix_zutil.buildroot.extract_nanvix_version_base` — extract base version from a suffixed tag
- :func:`~nanvix_zutil.buildroot.parse_semver_tuple` — parse semver string to tuple for comparison
- :mod:`nanvix_zutil.log` — structured logging helpers
- :class:`~nanvix_zutil.release.ArchiveFormat` — supported archive formats
- :data:`~nanvix_zutil.release.DEFAULT_FORMATS` — default archive formats
- :func:`~nanvix_zutil.release.package` — create release archives
- :func:`~nanvix_zutil.docker.is_windows` — Windows platform detection helper
- :func:`~nanvix_zutil.helpers.ensure_tool_installed` — verify an external CLI tool is on PATH
- :func:`~nanvix_zutil.helpers.sync_configs` — sync packaged configs into a Nanvix tree
- :func:`~nanvix_zutil.helpers.make_initrd` — build an initrd image
- :func:`~nanvix_zutil.helpers.run` — run a subprocess with standardized logging
"""

from nanvix_zutil.buildroot import (
    Dependency,
    Ref,
    RefKind,
    extract_nanvix_version,
    extract_nanvix_version_base,
    parse_semver_tuple,
    suffix_dep,
)
from nanvix_zutil.commands.info import NanvixInfo, get_nanvix_info
from nanvix_zutil.config import (
    CFG_DOCKER_IMAGE,
    CFG_GH_TOKEN,
    CFG_SYSROOT,
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_HOST,
    DEFAULT_MACHINE,
    DEFAULT_MEMORY_SIZE,
    DEFAULT_TARGET,
    Config,
    DeploymentMode,
    Host,
    Machine,
    MemorySize,
    Target,
)
from nanvix_zutil.docker import (
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    is_windows,
    remove_build_volume,
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
from nanvix_zutil.github import resolve_commit, resolve_release
from nanvix_zutil.helpers import (
    InitRdArgs,
    ensure_tool_installed,
    make_initrd,
    mkramfs,
    run,
    sync_configs,
)
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedAsset,
    ResolvedPackage,
    read_lockfile,
    serialize_lockfile,
    write_lockfile,
)
from nanvix_zutil.manifest import (
    Manifest,
    SdkPin,
    Toolchain,
    ToolchainKind,
    load_manifest,
)
from nanvix_zutil.release import DEFAULT_FORMATS, ArchiveFormat, package
from nanvix_zutil.resolver import BlockedResolution, is_stale, resolve
from nanvix_zutil.sdk import (
    SDK_RELEASE_ASSET,
    SdkImage,
    SdkProvenance,
    SdkRelease,
    SdkValidationError,
    parse_sdk_version,
    resolve_sdk_release,
    sdk_consumer_release_tag,
    validate_sdk_release,
    verify_sdk_image,
    verify_sdk_metadata,
)
from nanvix_zutil.script import ZScript
from nanvix_zutil.sysroot import Sysroot
from nanvix_zutil.testing import StandaloneTest, StandaloneTestFailure

__all__ = [
    "ArchiveFormat",
    "BlockedResolution",
    "CFG_DOCKER_IMAGE",
    "CFG_GH_TOKEN",
    "CFG_SYSROOT",
    "Config",
    "DeploymentMode",
    "DEFAULT_DEPLOYMENT_MODE",
    "DEFAULT_FORMATS",
    "DEFAULT_HOST",
    "DEFAULT_MACHINE",
    "DEFAULT_MEMORY_SIZE",
    "DEFAULT_TARGET",
    "Host",
    "Machine",
    "MemorySize",
    "Target",
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
    "SDK_RELEASE_ASSET",
    "SdkImage",
    "SdkPin",
    "SdkProvenance",
    "SdkRelease",
    "SdkValidationError",
    "SYSROOT_CONTAINER_PATH",
    "TOOLCHAIN_CONTAINER_PATH",
    "WORKSPACE_CONTAINER_PATH",
    "Sysroot",
    "Toolchain",
    "ToolchainKind",
    "ZScript",
    "is_windows",
    "remove_build_volume",
    "ensure_tool_installed",
    "extract_nanvix_version",
    "extract_nanvix_version_base",
    "get_nanvix_info",
    "is_stale",
    "load_manifest",
    "make_initrd",
    "package",
    "parse_semver_tuple",
    "read_lockfile",
    "serialize_lockfile",
    "resolve",
    "resolve_commit",
    "resolve_release",
    "resolve_sdk_release",
    "run",
    "suffix_dep",
    "sdk_consumer_release_tag",
    "sync_configs",
    "write_lockfile",
    "InitRdArgs",
    "StandaloneTest",
    "StandaloneTestFailure",
    "mkramfs",
    "parse_sdk_version",
    "validate_sdk_release",
    "verify_sdk_metadata",
    "verify_sdk_image",
]
