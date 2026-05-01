# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix runtime sysroot management.

:class:`Sysroot` downloads and verifies the Nanvix runtime artifact from
GitHub releases.  The artifact is a ``.tar.bz2`` archive identified by
machine, deployment mode, and memory-size configuration.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from nanvix_zutil import github, log
from nanvix_zutil.config import Config
from nanvix_zutil.config import DEFAULT_TARGET
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP

# ---------------------------------------------------------------------------
# Default location
# ---------------------------------------------------------------------------

_DEFAULT_SYSROOT_DIR = Path(".nanvix") / "sysroot"

# ---------------------------------------------------------------------------
# Sysroot repository / tag constants
# ---------------------------------------------------------------------------

_SYSROOT_REPO = "nanvix/nanvix"
_SYSROOT_ASSET_PREFIX = "nanvix-{target}-{machine}-{mode}-release-{mem}"
_WINDOWS_SYSROOT_ASSET_PREFIX = "nanvix-windows-{target}-{machine}-{mode}-release-{mem}"

# Host binaries needed from the Windows release to run VMs on Windows.
# kernel.elf is a *guest* binary (i686) — nanvixd.exe loads it directly.
WINDOWS_HOST_BINARIES = ("nanvixd.exe", "mkramfs.exe", "kernel.elf")


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
        sysroot_dir = dest if dest is not None else _DEFAULT_SYSROOT_DIR

        if sysroot_dir.exists():
            if sysroot_dir.is_dir():
                log.info(f"Sysroot already present at {sysroot_dir}")
                cached_tag = (
                    config.get("sysroot_tag", "") or "" if config is not None else ""
                )
                return Sysroot(sysroot_dir.resolve(), tag=cached_tag)
            log.fatal(
                f"Sysroot path '{sysroot_dir}' exists but is not a directory.",
                code=EXIT_MISSING_DEP,
                hint="Remove or rename this path and re-run"
                " `./z setup` to download the Nanvix sysroot.",
            )

        release = github.resolve_release(_SYSROOT_REPO, tag, gh_token, semver=True)
        tag_name = release.get("tag_name", "")
        resolved_tag = tag_name if isinstance(tag_name, str) else ""

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
        with tarfile.open(asset_path, "r:bz2") as tf:
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
    ) -> None:
        """Download Windows host binaries from the Nanvix release.

        On Windows, the sysroot from :meth:`download` contains only
        Linux ELF binaries.  This method downloads the matching Windows
        release asset (``.zip``) and extracts ``nanvixd.exe``,
        ``mkramfs.exe``, and ``kernel.elf`` into the sysroot ``bin/``
        directory.

        Skips silently if all required binaries are already present.
        """
        import zipfile

        bin_dir = self.path / "bin"
        if all((bin_dir / b).is_file() for b in WINDOWS_HOST_BINARIES):
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
                        import shutil

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

        import shutil

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
    # Verification
    # ------------------------------------------------------------------

    def verify(self, required_files: list[str]) -> None:
        """Assert that all required runtime files are present in the sysroot.

        Args:
            required_files: List of relative file paths that must exist under
                the sysroot directory.

        Raises:
            SystemExit: With exit code ``3`` if any required file is missing.
        """
        for rel_path in required_files:
            full_path = self.path / rel_path
            if not full_path.exists():
                log.fatal(
                    f"Required sysroot file '{rel_path}' not found at {self.path}",
                    code=EXIT_MISSING_DEP,
                    hint="Run `./z setup` to download the Nanvix sysroot.",
                )
        log.success("Sysroot verification passed")
