# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix runtime sysroot management.

:class:`Sysroot` downloads and verifies the Nanvix runtime artifact from
GitHub releases.  The artifact is an archive (``.tar.bz2``, ``.tar.gz``,
or ``.zip``) identified by machine, deployment mode, and memory-size
configuration.
"""

from __future__ import annotations

import shutil
import stat
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from nanvix_zutil import github, log
from nanvix_zutil.buildroot import Dependency
from nanvix_zutil.config import (
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_HOST,
    DEFAULT_MACHINE,
    DEFAULT_MEMORY_SIZE,
    DEFAULT_TARGET,
    Config,
)
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP
from nanvix_zutil.paths import nanvix_root
from nanvix_zutil.paths import sysroot as _default_sysroot_dir

# zipfile packs the Unix file mode into the high 16 bits of external_attr,
# a region the zip format itself leaves undefined. Shift to read it.
ZIP_MODE_SHIFT = 16

# ---------------------------------------------------------------------------
# Sysroot repository / tag constants
# ---------------------------------------------------------------------------

_SYSROOT_REPO = "nanvix/nanvix"
_SYSROOT_ASSET_PREFIX = "nanvix-{target}-{machine}-{mode}-release-{mem}"
_WINDOWS_SYSROOT_ASSET_PREFIX = "nanvix-windows-{target}-{machine}-{mode}-release-{mem}"

# Host binaries needed from the Windows release to run VMs on Windows.
# kernel.elf is a *guest* binary (i686) — nanvixd.exe loads it directly.
WINDOWS_HOST_BINARIES = ("nanvixd.exe", "mkramfs.exe", "mkimage.exe", "kernel.elf")


# ---------------------------------------------------------------------------
# Verbatim install helpers
# ---------------------------------------------------------------------------


def _unsafe(member: Path) -> bool:
    """Reject absolute paths and directory traversal."""
    return member.is_absolute() or ".." in member.parts


def _read_entries(source: Path) -> Iterator[tuple[Path, IO[bytes], int]]:
    """Yield ``(relative_path, reader, mode)`` for each file in *source*.

    *source* may be a directory tree, a tarball, or a zip archive.  Each
    ``reader`` is a binary file object the caller must close (streaming,
    so large members are never buffered whole).  Unsafe members (absolute
    or ``..``) are skipped; the low 9 mode bits are carried over so
    executables (e.g. ``bin/`` scripts) stay runnable.  Symlinks and
    hardlinks are resolved to real file copies, so all three source kinds
    materialise the same verbatim tree.
    """
    if source.is_dir():
        for src in source.rglob("*"):
            if src.is_file():
                rel = src.relative_to(source)
                yield rel, src.open("rb"), src.stat().st_mode & 0o777
        return
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            for info in zf.infolist():
                member = Path(info.filename)
                if info.is_dir() or _unsafe(member):
                    continue
                yield member, zf.open(info), stat.S_IMODE(
                    info.external_attr >> ZIP_MODE_SHIFT
                )
        return
    with tarfile.open(source, "r:*") as tf:
        for m in tf.getmembers():
            member = Path(m.name)
            if _unsafe(member):
                continue
            # extractfile follows sym/hardlinks (resolving them to real
            # files, matching the directory path) and returns None for
            # directories and special files.
            reader = tf.extractfile(m)
            if reader is None:
                continue
            yield member, reader, m.mode & 0o777


def install_contents(source: Path, root: Path) -> int:
    """Copy a dependency's contents verbatim into *root*.

    *source* may be a directory tree, a tarball, or a zip archive; the
    relative layout (``lib/``, ``include/``, ``share/``, …) is preserved.
    Returns the number of files written.
    """
    copied = 0
    for rel, reader, mode in _read_entries(source):
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with reader, dest.open("wb") as out:
            shutil.copyfileobj(reader, out)
        if mode:
            dest.chmod(mode)
        copied += 1
    return copied


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class Sysroot:
    """Manages the Nanvix runtime sysroot directory.

    Attributes:
        path: Absolute path to the sysroot directory.
        tag: Resolved tag name (e.g. ``"v0.12.277"``).
    """

    def __init__(self, path: Path, tag: str = "") -> None:
        """Initialise the Sysroot with an existing directory.

        Args:
            path: Path to the sysroot directory.
            tag: Resolved release tag name (set by :meth:`download`).
        """
        self.path = path
        self.tag = tag

    # ------------------------------------------------------------------
    # Factory / download
    # ------------------------------------------------------------------

    @staticmethod
    def download(
        machine: str,
        deployment_mode: str,
        memory_size: str,
        tag: str | int,
        gh_token: str | None = None,
        dest: Path | None = None,
        config: Config | None = None,
        target: str = DEFAULT_TARGET,
    ) -> "Sysroot":
        """Download and extract the Nanvix runtime artifact from GitHub releases.

        If the sysroot directory already exists the download is skipped.

        Args:
            machine: Target machine identifier (e.g. ``"microvm"``).
            deployment_mode: Deployment mode string (e.g.
                ``"standalone"``).
            memory_size: Memory size string (e.g. ``"256mb"``).
            tag: GitHub release tag to fetch (e.g. ``"v1.2.3"``).
            gh_token: Optional GitHub personal access token.
            dest: Directory where the sysroot will be extracted.  Defaults
                to ``.nanvix/sysroot`` relative to the current working
                directory.
            config: Optional :class:`Config` instance used to persist
                the resolved ``sysroot_tag``.  After a successful
                download the tag is written back to *config* and
                saved to disk.
            target: Target architecture (default: ``"x86"``).

        Returns:
            A :class:`Sysroot` pointing at the extracted directory.
        """
        sysroot_dir = dest if dest is not None else _default_sysroot_dir()

        # Fail early if the path exists but is not a directory.
        if sysroot_dir.exists() and not sysroot_dir.is_dir():
            log.fatal(
                f"Sysroot path '{sysroot_dir}' exists but is not a directory.",
                code=EXIT_MISSING_DEP,
                hint="Remove or rename this path and re-run"
                " `./z setup` to download the Nanvix sysroot.",
            )

        # Resolve the requested specifier to a canonical release tag so
        # that comparisons against the cached tag are format-independent
        # (e.g. "0.12.410" vs. "v0.12.410", or "latest").
        release = github.resolve_release(_SYSROOT_REPO, tag, gh_token, semver=True)
        tag_name = release.get("tag_name", "")
        resolved_tag = tag_name if isinstance(tag_name, str) else ""

        if sysroot_dir.is_dir():
            cached_tag = (
                (config.get("sysroot_tag", "") or "") if config is not None else ""
            )
            if cached_tag and resolved_tag == cached_tag:
                log.info(f"Sysroot already present at {sysroot_dir}")
                return Sysroot(sysroot_dir.resolve(), tag=cached_tag)
            log.info(
                f"Sysroot tag mismatch (cached={cached_tag!r},"
                f" resolved={resolved_tag!r}), re-downloading…"
            )
            shutil.rmtree(sysroot_dir)

        asset_prefix = _SYSROOT_ASSET_PREFIX.format(
            target=target,
            machine=machine,
            mode=deployment_mode,
            mem=memory_size,
        )

        cache_dir = sysroot_dir.parent / "cache"
        asset_path = github.download_release_asset(
            repo=_SYSROOT_REPO,
            version_specifier=tag,
            asset_name=asset_prefix,
            dest=cache_dir,
            gh_token=gh_token,
            match_prefix=True,
            semver=True,
            _release=release,
        )

        log.info(f"Extracting sysroot from {asset_path.name}…")
        sysroot_dir.mkdir(parents=True, exist_ok=True)
        if zipfile.is_zipfile(asset_path):
            resolved_sysroot = sysroot_dir.resolve()
            with zipfile.ZipFile(asset_path, "r") as zf:
                for member in zf.namelist():
                    target_path = (sysroot_dir / member).resolve()
                    if not target_path.is_relative_to(resolved_sysroot):
                        continue
                    zf.extract(member, path=sysroot_dir)
        else:
            with tarfile.open(asset_path, "r:*") as tf:
                tf.extractall(path=sysroot_dir, filter="data")

        log.success(f"Sysroot extracted to {sysroot_dir}")
        if config is not None:
            config.set("sysroot_tag", resolved_tag)
            config.save()
        return Sysroot(sysroot_dir.resolve(), tag=resolved_tag)

    # ------------------------------------------------------------------
    # Windows host binaries
    # ------------------------------------------------------------------

    def download_windows_binaries(
        self,
        machine: str,
        deployment_mode: str,
        memory_size: str,
        gh_token: str | None = None,
        target: str = DEFAULT_TARGET,
        config: Config | None = None,
    ) -> None:
        """Download Windows host binaries from the Nanvix release.

        On Windows, the sysroot from :meth:`download` contains only
        Linux ELF binaries.  This method downloads the matching Windows
        release asset (``.zip``) and extracts ``nanvixd.exe``,
        ``mkramfs.exe``, and ``kernel.elf`` into the sysroot ``bin/``
        directory.

        Skips silently if all required binaries are already present
        and the persisted tag matches the current sysroot tag.  When
        *config* is ``None`` the check falls back to file presence
        alone (no tag comparison).

        Args:
            machine: Target machine identifier.
            deployment_mode: Deployment mode string.
            memory_size: Memory size string.
            gh_token: Optional GitHub personal access token.
            target: Target architecture (default: ``"x86"``).
            config: Optional :class:`Config` used to persist the
                ``windows_binaries_tag``.  When provided the tag is
                checked on entry and written back on success.
        """
        import zipfile

        bin_dir = self.path / "bin"
        all_present = all((bin_dir / b).is_file() for b in WINDOWS_HOST_BINARIES)
        if all_present:
            if config is None:
                log.info("Windows host binaries already present in sysroot")
                return
            cached_win_tag = config.get("windows_binaries_tag", "") or ""
            if cached_win_tag == self.tag:
                log.info("Windows host binaries already present in sysroot")
                return

        tag = self.tag
        if not tag:
            log.warning("No sysroot tag available — cannot download Windows binaries")
            return

        asset_prefix = _WINDOWS_SYSROOT_ASSET_PREFIX.format(
            target=target,
            machine=machine,
            mode=deployment_mode,
            mem=memory_size,
        )

        release = github.resolve_release(_SYSROOT_REPO, tag, gh_token, semver=True)

        cache_dir = self.path.parent / "cache"
        asset_path = github.download_release_asset(
            repo=_SYSROOT_REPO,
            version_specifier=tag,
            asset_name=asset_prefix,
            dest=cache_dir,
            gh_token=gh_token,
            match_prefix=True,
            semver=True,
            _release=release,
            allow_missing=True,
        )
        if asset_path is None:
            log.fatal(
                f"Windows asset matching '{asset_prefix}' not found in release {tag}",
                code=EXIT_MISSING_DEP,
                hint="Check that the Nanvix release includes Windows assets.",
            )

        bin_dir.mkdir(parents=True, exist_ok=True)
        wanted = set(WINDOWS_HOST_BINARIES)
        with zipfile.ZipFile(asset_path) as zf:
            for entry in zf.namelist():
                basename = Path(entry).name
                if basename in wanted:
                    dest = bin_dir / basename
                    with zf.open(entry) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    log.info(f"Extracted {basename} to sysroot/bin/")

        missing = [b for b in WINDOWS_HOST_BINARIES if not (bin_dir / b).is_file()]
        if missing:
            log.fatal(
                f"Windows binaries missing after download: {', '.join(missing)}",
                code=EXIT_MISSING_DEP,
                hint=(
                    "Delete `.nanvix/sysroot` and run `./z setup` again, or "
                    "check that the Windows release asset contains the expected "
                    "host binaries."
                ),
            )
        else:
            if config is not None:
                config.set("windows_binaries_tag", tag)
                config.save()
            log.success("Windows host binaries installed")

    # ------------------------------------------------------------------
    # Local overlay
    # ------------------------------------------------------------------

    def overlay_local_nanvix(self, local_path: Path) -> None:
        """Overlay locally-built Nanvix artifacts on top of the sysroot.

        Walks the local directory and copies any files that match the
        sysroot layout (``bin/`` and ``lib/`` subdirectories) into the
        sysroot, overriding downloaded artifacts.  This enables
        development workflows where nanvixd, mkramfs, uservm, etc. are
        built from a local checkout.

        Args:
            local_path: Absolute path to the local Nanvix build output
                directory.  Expected to mirror the sysroot layout
                (``bin/nanvixd.elf``, ``lib/libposix.a``, etc.).

        Raises:
            SystemExit: If *local_path* does not exist or is not a
                directory.
        """
        if not local_path.is_dir():
            log.fatal(
                f"--with-nanvix path is not a directory: {local_path}",
                code=EXIT_MISSING_DEP,
            )

        overlaid: list[str] = []
        for subdir in ("bin", "lib"):
            src_dir = local_path / subdir
            if not src_dir.is_dir():
                continue
            dst_dir = self.path / subdir
            dst_dir.mkdir(parents=True, exist_ok=True)
            for src_file in src_dir.iterdir():
                if src_file.is_file():
                    dst_file = dst_dir / src_file.name
                    shutil.copy2(src_file, dst_file)
                    overlaid.append(f"{subdir}/{src_file.name}")

        if overlaid:
            log.info(f"Overlaid {len(overlaid)} local artifact(s) from {local_path}")
            for name in sorted(overlaid):
                log.info(f"  → {name}")
        else:
            log.warning(
                f"No bin/ or lib/ artifacts found in {local_path} — "
                "sysroot unchanged"
            )

    # ------------------------------------------------------------------
    # Build-time dependency installation
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
        A missing archive is fatal — no fallback to the legacy naming.

        Args:
            dep: The :class:`~nanvix_zutil.buildroot.Dependency` descriptor.
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
        install_contents(asset_path, self.path)
        log.success(f"Installed {dep.name} into sysroot")

    def install_local_nanvix(self, dep: Dependency, local_path: Path) -> bool:
        """Install a dependency from a local Nanvix build directory.

        Looks for ``<local_path>/deps/<dep.name>/``; if present, its entire
        tree is copied verbatim into the sysroot (same rules as archive
        extraction).

        Args:
            dep: The :class:`~nanvix_zutil.buildroot.Dependency` descriptor.
            local_path: Absolute path to the local Nanvix build output.

        Returns:
            ``True`` if local artifacts were found and installed,
            ``False`` otherwise (caller should fall back to GitHub).
        """
        dep_dir = local_path / "deps" / dep.name
        if not dep_dir.is_dir():
            return False
        copied = install_contents(dep_dir, self.path)
        if copied:
            log.info(f"Installed {dep.name} from local path: {dep_dir}")
        return copied > 0

    def install_local_archive(self, dep: Dependency, manifest_path: Path) -> None:
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

        copied = install_contents(dev_dir, self.path)
        log.success(f"Copied {copied} file(s) for {dep.name} from {dev_dir}")

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, required_files: list[str]) -> None:
        """Assert that all required files are present in the sysroot.

        Covers both runtime artifacts and build-time dependency files
        (headers, static libraries) installed by :meth:`install_dep`.

        Args:
            required_files: Sysroot-relative paths that must exist
                (e.g. ``"bin/nanvixd.elf"``, ``"lib/libz.a"``).

        Raises:
            SystemExit: With exit code ``3`` if any required file is missing.
        """
        for rel_path in required_files:
            rel = Path(rel_path)
            if _unsafe(rel):
                log.fatal(
                    f"Required file '{rel_path}' must be a sysroot-relative path",
                    code=EXIT_MISSING_DEP,
                )
            if not (self.path / rel).exists():
                log.fatal(
                    f"Required sysroot file '{rel_path}' not found at {self.path}",
                    code=EXIT_MISSING_DEP,
                    hint="Run `./z setup` to download the Nanvix sysroot.",
                )
        log.success("Sysroot verification passed")
