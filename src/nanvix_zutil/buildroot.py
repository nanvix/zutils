# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Build-time dependency management for nanvix_zutil consumers.

:class:`Buildroot` manages a directory that collects headers and static
libraries required to compile a consumer repository.  :class:`Dependency`
describes a single library fetched from a GitHub release.
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass, replace as _dc_replace
from enum import Enum
from pathlib import Path

from nanvix_zutil import github, log
from nanvix_zutil.config import (
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_MACHINE,
    DEFAULT_MEMORY_SIZE,
)
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP

# ---------------------------------------------------------------------------
# Default locations
# ---------------------------------------------------------------------------

_DEFAULT_BUILDROOT_DIR = Path(".nanvix") / "buildroot"


# ---------------------------------------------------------------------------
# Tarball path helpers
# ---------------------------------------------------------------------------


def _relative_to_segment(member_path: Path, segment: str) -> str:
    """Return the path portion after *segment* in *member_path*.

    If *segment* is not found, return the bare filename.

    Args:
        member_path: Full archive member path
            (e.g. ``sysroot/include/openssl/ssl.h``).
        segment: Directory segment to search for
            (e.g. ``"include"`` or ``"lib"``).

    Returns:
        Relative path after the segment, or the bare filename as
        fallback.
    """
    parts = member_path.parts
    try:
        idx = parts.index(segment)
        return str(Path(*parts[idx + 1 :]))
    except ValueError:
        return member_path.name


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


def suffix_dep(dep: Dependency, version: str) -> Dependency:
    """Return a copy of *dep* with its VERSION ref suffixed with *version*.

    If *dep* has a non-VERSION ref kind, or its value already contains
    ``-nanvix-`` (e.g. from an env-var override), it is returned unchanged.

    Args:
        dep: The dependency to suffix.
        version: The nanvix sysroot version to append
            (e.g. ``"0.12.277"``).

    Returns:
        A new :class:`Dependency` with the suffixed ref, or the
        original if no suffixing is needed.
    """
    if dep.ref.kind == RefKind.VERSION and isinstance(dep.ref.value, str):
        if "-nanvix-" in dep.ref.value:
            return dep
        return _dc_replace(
            dep,
            ref=Ref(
                kind=dep.ref.kind,
                value=f"{dep.ref.value}-nanvix-{version}",
            ),
        )
    return dep


def extract_nanvix_version(suffixed_tag: str) -> str | None:
    """Extract the nanvix version from a suffixed tag.

    Given a tag like ``"1.3.1-nanvix-0.12.291"``, returns ``"0.12.291"``.
    Returns ``None`` if the tag does not contain the ``-nanvix-`` infix.

    Args:
        suffixed_tag: A release tag that may contain ``-nanvix-{version}``.

    Returns:
        The nanvix version string, or ``None``.
    """
    marker = "-nanvix-"
    idx = suffixed_tag.find(marker)
    if idx == -1:
        return None
    return suffixed_tag[idx + len(marker) :]


def extract_nanvix_version_base(suffixed_value: str) -> str | None:
    """Extract the base package version from a suffixed ref value.

    Given ``"1.3.1-nanvix-0.12.291"``, returns ``"1.3.1"``.
    Returns ``None`` if the value does not contain ``-nanvix-``.

    Args:
        suffixed_value: A ref value that may contain ``-nanvix-{version}``.

    Returns:
        The base package version string, or ``None``.
    """
    marker = "-nanvix-"
    idx = suffixed_value.find(marker)
    if idx == -1:
        return None
    return suffixed_value[:idx]


def parse_semver_tuple(version: str) -> tuple[int, ...]:
    """Parse a semver string into a tuple of integers for comparison.

    Args:
        version: A dotted version string (e.g. ``"0.12.291"``).

    Returns:
        Tuple of integer parts (e.g. ``(0, 12, 291)``).

    Raises:
        ValueError: If any part is not an integer.
    """
    return tuple(int(p) for p in version.split("."))


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
        machine: str = DEFAULT_MACHINE,
        deployment_mode: str = DEFAULT_DEPLOYMENT_MODE,
        memory_size: str = DEFAULT_MEMORY_SIZE,
        gh_token: str | None = None,
        *,
        _release: dict[str, object] | None = None,
    ) -> None:
        """Download a dependency release and install its libraries and headers.

        The release asset is downloaded into ``.nanvix/cache/`` and then
        extracted.  Selected ``.a`` and ``.h`` files are copied into
        ``<buildroot>/lib/`` and ``<buildroot>/include/`` respectively.

        Args:
            dep: The :class:`Dependency` descriptor.
            machine: Target machine identifier.
            deployment_mode: Deployment mode string.
            memory_size: Memory size string.
            gh_token: Optional GitHub token.
            _release: Pre-resolved release metadata dictionary.  When
                provided, the release resolution step is skipped (avoids
                redundant GitHub API calls when the caller has already
                resolved the release).
        """
        asset_name = dep.artifact_pattern.format(
            name=dep.name,
            machine=machine,
            mode=deployment_mode,
            mem=memory_size,
        )

        cache_dir = self.path.parent / "cache"

        asset_path = github.download_release_asset(
            repo=dep.repo,
            version_specifier=dep.ref.value,
            asset_name=asset_name,
            dest=cache_dir,
            gh_token=gh_token,
            _release=_release,
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
                            member.name = _relative_to_segment(member_path, "lib")
                            tf.extract(member, path=self.path / "lib", filter="data")
                    elif member_path.suffix == ".h":
                        if dep.install_headers is None or member_path.name in (
                            dep.install_headers
                        ):
                            member.name = _relative_to_segment(member_path, "include")
                            tf.extract(
                                member, path=self.path / "include", filter="data"
                            )

        log.success(f"Installed {dep.name} into buildroot")

    def install_local_nanvix(
        self,
        dep: Dependency,
        local_path: Path,
    ) -> bool:
        """Install a dependency from a local Nanvix build directory.

        Looks for ``<local_path>/deps/<dep.name>/`` containing ``lib/``
        and/or ``include/`` subdirectories.  If found, copies the
        matching artifacts into the buildroot.

        Args:
            dep: The :class:`Dependency` descriptor.
            local_path: Absolute path to the local Nanvix build output.

        Returns:
            ``True`` if local artifacts were found and installed,
            ``False`` otherwise (caller should fall back to GitHub).
        """
        import shutil

        dep_dir = local_path / "deps" / dep.name
        if not dep_dir.is_dir():
            return False

        installed = False
        lib_dir = dep_dir / "lib"
        if lib_dir.is_dir():
            dst_lib = self.path / "lib"
            dst_lib.mkdir(parents=True, exist_ok=True)
            for src_file in lib_dir.iterdir():
                if src_file.is_file() and src_file.suffix == ".a":
                    if dep.install_libs is None or src_file.name in dep.install_libs:
                        shutil.copy2(src_file, dst_lib / src_file.name)
                        installed = True

        include_dir = dep_dir / "include"
        if include_dir.is_dir():
            dst_inc = self.path / "include"
            dst_inc.mkdir(parents=True, exist_ok=True)
            for src_file in include_dir.rglob("*"):
                if src_file.is_file() and src_file.suffix == ".h":
                    if (
                        dep.install_headers is None
                        or src_file.name in dep.install_headers
                    ):
                        rel = src_file.relative_to(include_dir)
                        dst_file = dst_inc / rel
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)
                        installed = True

        if installed:
            log.info(f"Installed {dep.name} from local path: {dep_dir}")
        return installed

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
