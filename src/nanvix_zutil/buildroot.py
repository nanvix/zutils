# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Build-time dependency management for nanvix_zutil consumers.

:class:`Buildroot` manages a directory that collects headers and static
libraries required to compile a consumer repository.  :class:`Dependency`
describes a single library fetched from a GitHub release.
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from nanvix_zutil import github, log
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP

# ---------------------------------------------------------------------------
# Default locations
# ---------------------------------------------------------------------------

_DEFAULT_BUILDROOT_DIR = Path(".nanvix") / "buildroot"


# ---------------------------------------------------------------------------
# Version reference
# ---------------------------------------------------------------------------


class RefKind(Enum):
    """Discriminator for version reference types."""

    TAG = "tag"
    COMMITISH = "commitish"
    ID = "id"
    VERSION = "version"


@dataclass
class Ref:
    """A tagged union for version references.

    Attributes:
        kind: The specifier type — :attr:`RefKind.TAG` (exact tag match),
            :attr:`RefKind.COMMITISH` (match ``target_commitish``),
            :attr:`RefKind.ID` (direct release fetch), or
            :attr:`RefKind.VERSION` (suffixed with nanvix version).
        value: The version string, tag name, commitish, or release ID.
    """

    kind: RefKind
    value: str | int


# ---------------------------------------------------------------------------
# Dependency descriptor
# ---------------------------------------------------------------------------


@dataclass
class Dependency:
    """A library dependency fetched from a GitHub release.

    Attributes:
        name: Short library name (e.g. ``"zlib"``).
        repo: GitHub repository in ``owner/name`` format
            (e.g. ``"nanvix/zlib"``).
        ref: Version reference — one of tag, commitish, ID, or version.
        artifact_pattern: ``str.format``-style template for the asset file
            name.  Interpolated keys: ``{name}``, ``{machine}``,
            ``{mode}``, ``{mem}``.
        install_libs: List of ``.a`` file names to copy into
            ``<buildroot>/lib/``.  ``None`` copies all ``.a`` files found.
        install_headers: List of header file names to copy into
            ``<buildroot>/include/``.  ``None`` copies all ``.h`` files found.
    """

    name: str
    repo: str
    ref: Ref
    artifact_pattern: str = "{name}-{machine}-{mode}-{mem}.tar.bz2"
    install_libs: list[str] | None = None
    install_headers: list[str] | None = None


# ---------------------------------------------------------------------------
# Buildroot
# ---------------------------------------------------------------------------


class Buildroot:
    """Manages the build-time dependency root (headers and static libraries).

    Attributes:
        path: Absolute path to the buildroot directory.
    """

    def __init__(self, path: Path) -> None:
        """Initialise the Buildroot with an existing directory.

        Args:
            path: Path to the buildroot directory.
        """
        self.path = path

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    def create(dest: Path | None = None) -> "Buildroot":
        """Create (or locate) the buildroot directory and return a
        :class:`Buildroot` instance.

        Args:
            dest: Directory to use.  Defaults to
                ``.nanvix/buildroot`` relative to the current working
                directory.

        Returns:
            A :class:`Buildroot` pointing at *dest*.
        """
        path = dest if dest is not None else _DEFAULT_BUILDROOT_DIR
        (path / "lib").mkdir(parents=True, exist_ok=True)
        (path / "include").mkdir(parents=True, exist_ok=True)
        log.info(f"Buildroot at {path}")
        return Buildroot(path.resolve())

    # ------------------------------------------------------------------
    # Dependency installation
    # ------------------------------------------------------------------

    def install_dep(
        self,
        dep: Dependency,
        machine: str = "hyperlight",
        deployment_mode: str = "multi-process",
        memory_size: str = "128mb",
        gh_token: str | None = None,
        sysroot_commitish: str | None = None,
    ) -> None:
        """Download a dependency release and install its libraries and headers.

        The release asset is downloaded into ``.nanvix/cache/`` and then
        extracted.  Selected ``.a`` and ``.h`` files are copied into
        ``<buildroot>/lib/`` and ``<buildroot>/include/`` respectively.

        For ``VERSION`` refs, if the primary tag is not found and
        *sysroot_commitish* is provided, a fallback tag is tried by
        replacing the sysroot version suffix with the commitish hash.

        Args:
            dep: The :class:`Dependency` descriptor.
            machine: Target machine identifier.
            deployment_mode: Deployment mode string.
            memory_size: Memory size string.
            gh_token: Optional GitHub token.
            sysroot_commitish: Short commitish hash of the resolved
                sysroot release, used for VERSION ref fallback.
        """
        asset_name = dep.artifact_pattern.format(
            name=dep.name,
            machine=machine,
            mode=deployment_mode,
            mem=memory_size,
        )

        cache_dir = self.path.parent / "cache"

        if dep.ref.kind == RefKind.VERSION and sysroot_commitish:
            result = github.download_release_asset(
                repo=dep.repo,
                version_specifier=dep.ref.value,
                asset_name=asset_name,
                dest=cache_dir,
                gh_token=gh_token,
                allow_missing=True,
            )
            if result is not None:
                asset_path = result
            else:
                primary = str(dep.ref.value)
                parts = primary.rsplit("-nanvix-", 1)
                if len(parts) != 2:
                    log.fatal(
                        f"Asset not found for {dep.name}@{dep.ref.value}",
                        code=EXIT_MISSING_DEP,
                    )
                fallback_tag = f"{parts[0]}-nanvix-{sysroot_commitish}"
                log.warning(
                    f"Tag '{primary}' not found," f" trying '{fallback_tag}'\u2026"
                )
                asset_path = github.download_release_asset(
                    repo=dep.repo,
                    version_specifier=fallback_tag,
                    asset_name=asset_name,
                    dest=cache_dir,
                    gh_token=gh_token,
                )
        else:
            asset_path = github.download_release_asset(
                repo=dep.repo,
                version_specifier=dep.ref.value,
                asset_name=asset_name,
                dest=cache_dir,
                gh_token=gh_token,
            )

        log.info(f"Extracting {asset_name}...")
        with tarfile.open(asset_path, "r:bz2") as tf:
            for member in tf.getmembers():
                member_path = Path(member.name)
                if member.isfile():
                    if member_path.suffix == ".a":
                        if dep.install_libs is None or member_path.name in (
                            dep.install_libs
                        ):
                            member.name = member_path.name
                            tf.extract(member, path=self.path / "lib", filter="data")
                    elif member_path.suffix == ".h":
                        if dep.install_headers is None or member_path.name in (
                            dep.install_headers
                        ):
                            member.name = member_path.name
                            tf.extract(
                                member, path=self.path / "include", filter="data"
                            )

        log.success(f"Installed {dep.name} into buildroot")

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, required_libs: list[str]) -> None:
        """Assert that all required build-time library files are present.

        Args:
            required_libs: List of ``.a`` file names that must exist under
                ``<buildroot>/lib/``.

        Raises:
            SystemExit: With exit code ``3`` if any required file is missing.
        """
        for lib in required_libs:
            lib_path = self.path / "lib" / lib
            if not lib_path.exists():
                log.fatal(
                    f"Required library '{lib}' not found in buildroot at {self.path}",
                    code=EXIT_MISSING_DEP,
                    hint="Run `./z setup` to download build-time dependencies.",
                )
        log.success("Buildroot verification passed")
