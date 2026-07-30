# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Build-time dependency management for nanvix_zutil consumers.

:class:`Buildroot` manages a directory that collects headers and static
libraries required to compile a consumer repository.  :class:`Dependency`
describes a single library fetched from a GitHub release.
"""

from __future__ import annotations

import shutil
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from enum import Enum
from pathlib import Path

from nanvix_zutil import github, log
from nanvix_zutil.config import (
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_HOST,
    DEFAULT_MACHINE,
    DEFAULT_MEMORY_SIZE,
    DEFAULT_TARGET,
)
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP
from nanvix_zutil.paths import nanvix_root, sysroot
from nanvix_zutil.release import DEV_ARCHIVE_SUFFIX

# zipfile packs the Unix file mode into the high 16 bits of external_attr,
# a region the zip format itself leaves undefined. Shift to read/write it.
ZIP_MODE_SHIFT = 16

# ---------------------------------------------------------------------------
# Verbatim copy helper
# ---------------------------------------------------------------------------


def _copy_local_dep_tree(source_dir: Path) -> int:
    """Copy every file under *source_dir* verbatim into the sysroot.

    Relative paths are preserved, so a strict ``lib/``/``include/``/``share/``
    layout lands intact in the sysroot.  Returns the number of files copied.
    """
    copied = 0
    for src in source_dir.rglob("*"):
        if not src.is_file():
            continue
        dest = sysroot() / src.relative_to(source_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied += 1
    return copied


# ---------------------------------------------------------------------------
# Version reference
# ---------------------------------------------------------------------------


class RefKind(Enum):
    """Discriminator for version reference types."""

    TAG = "tag"
    COMMITISH = "commitish"
    ID = "id"
    VERSION = "version"
    LOCAL = "local"


@dataclass
class Ref:
    """A tagged union for version references.

    Attributes:
        kind: The specifier type — :attr:`RefKind.TAG` (exact tag match),
            :attr:`RefKind.COMMITISH` (match ``target_commitish``),
            :attr:`RefKind.ID` (direct release fetch),
            :attr:`RefKind.VERSION` (suffixed with nanvix version), or
            :attr:`RefKind.LOCAL` (filesystem path, no GitHub resolution).
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
            name.  Interpolated keys: ``{name}``, ``{host}``, ``{arch}``,
            ``{machine}``, ``{mode}``, ``{mem}``.  Default targets the
            standardised ``-dev`` archive produced by
            ``nanvix-zutil release``.
    """

    name: str
    repo: str
    ref: Ref
    artifact_pattern: str = (
        "{name}-{host}-{arch}-{machine}-{mode}-{mem}" + DEV_ARCHIVE_SUFFIX
    )


def suffix_dep(dep: Dependency, version: str) -> Dependency:
    """Return a copy of *dep* with its VERSION ref suffixed with *version*.

    If *dep* has a non-VERSION ref kind (e.g. TAG, COMMITISH, ID, or
    LOCAL), or its value already contains ``-nanvix-`` (e.g. from an
    env-var override), it is returned unchanged.

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
    """Manages the build-time dependency root (headers and static libraries)."""

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    def create() -> "Buildroot":
        """Ensure the sysroot ``lib/`` and ``include/`` directories
        exist and return a :class:`Buildroot` instance.

        Build-time dependencies (headers and static archives) land in
        the sysroot alongside the runtime artifacts extracted by
        :class:`~nanvix_zutil.sysroot.Sysroot`.

        Returns:
            A :class:`Buildroot` pointing at the sysroot.
        """
        br = sysroot()
        (br / "lib").mkdir(parents=True, exist_ok=True)
        (br / "include").mkdir(parents=True, exist_ok=True)
        log.info(f"Buildroot deps installing into {br}")
        return Buildroot()

    # ------------------------------------------------------------------
    # Dependency installation
    # ------------------------------------------------------------------

    def install_dep(
        self,
        dep: Dependency,
        *,
        host: str = DEFAULT_HOST,
        target: str = DEFAULT_TARGET,
        machine: str = DEFAULT_MACHINE,
        deployment_mode: str = DEFAULT_DEPLOYMENT_MODE,
        memory_size: str = DEFAULT_MEMORY_SIZE,
        gh_token: str | None = None,
        _release: dict[str, object] | None = None,
    ) -> None:
        """Download a dependency release and install its contents.

        The release asset is downloaded into ``.nanvix/cache/`` and then
        unpacked verbatim into the sysroot, preserving the archive's
        directory layout (``lib/``, ``include/``, ``share/``, …).

        Dependencies are assumed to publish a standardised ``-dev`` archive.
        A missing archive is fatal — no fallback to the legacy
        naming.

        Args:
            dep: The :class:`Dependency` descriptor.
            host: Development host operating system.
            target: Target CPU architecture.
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
            host=host,
            arch=target,
            machine=machine,
            mode=deployment_mode,
            mem=memory_size,
        )

        cache_dir = nanvix_root() / "cache"

        asset_path = github.download_release_asset(
            repo=dep.repo,
            version_specifier=dep.ref.value,
            asset_name=asset_name,
            dest=cache_dir,
            gh_token=gh_token,
            match_prefix=True,
            _release=_release,
        )

        log.info(f"Extracting {asset_path.name}...")
        if zipfile.is_zipfile(asset_path):
            self._extract_dep_zip(asset_path)
        else:
            self._extract_dep_tar(asset_path)

        log.success(f"Installed {dep.name} into buildroot")

    # ------------------------------------------------------------------
    # Archive extraction helpers
    # ------------------------------------------------------------------

    def _extract_dep_tar(self, asset_path: Path) -> None:
        """Unpack a tarball verbatim into the sysroot."""
        with tarfile.open(asset_path, "r:*") as tf:
            tf.extractall(sysroot(), filter="data")

    def _extract_dep_zip(self, asset_path: Path) -> None:
        """Unpack a zip archive verbatim into the sysroot."""
        with zipfile.ZipFile(asset_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                member_path = Path(info.filename)
                # Reject absolute paths and directory traversal.
                if member_path.is_absolute() or ".." in member_path.parts:
                    continue
                dest = sysroot() / member_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                # zipfile does not restore Unix permission bits; carry over
                # the stored mode (e.g. executable bin/ scripts) verbatim.
                mode = stat.S_IMODE(info.external_attr >> ZIP_MODE_SHIFT)
                if mode:
                    dest.chmod(mode)

    def install_local_nanvix(
        self,
        dep: Dependency,
        local_path: Path,
    ) -> bool:
        """Install a dependency from a local Nanvix build directory.

        Looks for ``<local_path>/deps/<dep.name>/``; if present, its entire
        tree is copied verbatim into the sysroot (same rules as archive
        extraction).

        Args:
            dep: The :class:`Dependency` descriptor.
            local_path: Absolute path to the local Nanvix build output.

        Returns:
            ``True`` if local artifacts were found and installed,
            ``False`` otherwise (caller should fall back to GitHub).
        """
        dep_dir = local_path / "deps" / dep.name
        if not dep_dir.is_dir():
            return False
        copied = _copy_local_dep_tree(dep_dir)
        if copied:
            log.info(f"Installed {dep.name} from local path: {dep_dir}")
        return copied > 0

    def install_local_archive(
        self,
        dep: Dependency,
        manifest_path: Path,
    ) -> None:
        """Install a dependency from a sibling consumer's staged dev tree.

        Copies ``<manifest_path>/../out/staging/dev/`` — the tree the
        sibling's ``release`` step packs into the dev archive,
        byte-identical to the archive contents — verbatim into the
        sysroot, the same way the released archive is unpacked.

        Fatal (``EXIT_MISSING_DEP``) if the staging tree is absent;
        callers should run ``./z build`` against *manifest_path* first.
        """
        dev_dir = manifest_path.parent / "out" / "staging" / "dev"
        if not dev_dir.is_dir():
            log.fatal(
                f"local dep '{dep.name}': no staged dev tree at {dev_dir}",
                code=EXIT_MISSING_DEP,
                hint=f"Run `./z build` for {manifest_path} first.",
            )

        copied = _copy_local_dep_tree(dev_dir)
        log.success(f"Copied {copied} file(s) for {dep.name} from {dev_dir}")

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, required_files: list[str]) -> None:
        """Assert that all required build-time files are present.

        Args:
            required_files: Sysroot-relative paths that must exist
                (e.g. ``"lib/libz.a"``, ``"include/zlib.h"``).

        Raises:
            SystemExit: With exit code ``3`` if any required file is missing.
        """
        root = sysroot()
        for rel in required_files:
            path = Path(rel)
            if path.is_absolute() or ".." in path.parts:
                log.fatal(
                    f"Required file '{rel}' must be a sysroot-relative path",
                    code=EXIT_MISSING_DEP,
                )
            if not (root / path).exists():
                log.fatal(
                    f"Required file '{rel}' not found in sysroot at {root}",
                    code=EXIT_MISSING_DEP,
                    hint="Run `./z setup` to download build-time dependencies.",
                )
        log.success("Buildroot verification passed")
