# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Release artifact packaging."""

from __future__ import annotations

import os
import tarfile
import zipfile
from enum import Enum
from pathlib import Path
from shutil import rmtree
from typing import Literal

from nanvix_zutil import EXIT_MISSING_DEP, load_manifest, log, paths
from nanvix_zutil.exitcodes import EXIT_GENERAL_ERROR

# ---------------------------------------------------------------------------
# Archive format enum
# ---------------------------------------------------------------------------


class ArchiveFormat(Enum):
    """Supported archive formats.

    Attributes:
        TAR_GZ: gzip-compressed tarball (``.tar.gz``).
        TAR_BZ2: bzip2-compressed tarball (``.tar.bz2``).
        ZIP: ZIP archive (``.zip``).
    """

    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    ZIP = "zip"

    @property
    def extension(self) -> str:
        """Return the file extension including the leading dot.

        Returns:
            The extension string (e.g. ``".tar.gz"``).
        """
        return f".{self.value}"


#: Default formats produced by :func:`package`.
DEFAULT_FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.TAR_GZ, ArchiveFormat.ZIP)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_tarball(source: Path, dest: Path, compression: Literal["gz", "bz2"]) -> Path:
    """Create a tarball from *source* directory.

    All files are added relative to the archive root (i.e. paths inside the
    archive mirror the directory structure under *source*).

    Args:
        source: Directory whose contents will be archived.
        dest: Full path to the output tarball (e.g. ``dist/foo.tar.gz``).
        compression: Compression mode — ``"gz"`` for gzip or ``"bz2"`` for
            bzip2.

    Returns:
        *dest* after the tarball has been written.

    Note:
        Symlinks are excluded from the archive. Symlinked directories are not
        traversed.
    """
    mode: Literal["w:gz", "w:bz2"] = "w:gz" if compression == "gz" else "w:bz2"
    source_resolved = source.resolve()

    with tarfile.open(dest, mode) as tf:
        # Use os.walk with followlinks=False to prevent symlink directory traversal
        for root, dirs, files in os.walk(source, followlinks=False):
            root_path = Path(root)

            # Security check: ensure we haven't escaped the source directory
            try:
                root_resolved = root_path.resolve()
                root_resolved.relative_to(source_resolved)
            except ValueError:
                # Path has escaped source directory, skip it
                continue

            # Add regular files (exclude symlinks for security)
            for file_name in files:
                file_path = root_path / file_name
                if not file_path.is_symlink() and file_path.is_file():
                    tf.add(
                        file_path,
                        arcname=file_path.relative_to(source).as_posix(),
                        recursive=False,
                    )

            # Add directories themselves (exclude symlinks for security)
            for dir_name in dirs:
                dir_path = root_path / dir_name
                if not dir_path.is_symlink() and dir_path.is_dir():
                    tf.add(
                        dir_path,
                        arcname=dir_path.relative_to(source).as_posix(),
                        recursive=False,
                    )
    return dest


def _build_zip(source: Path, dest: Path) -> Path:
    """Create a ZIP archive from *source* directory.

    All files are added relative to the archive root, matching the layout
    produced by :func:`_build_tarball`.

    Args:
        source: Directory whose contents will be archived.
        dest: Full path to the output ZIP file (e.g. ``dist/foo.zip``).

    Returns:
        *dest* after the ZIP has been written.

    Note:
        Symlinks are excluded from the archive. Symlinked directories are not
        traversed.
    """
    source_resolved = source.resolve()

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        # Use os.walk with followlinks=False to prevent symlink directory traversal
        for root, _dirs, files in os.walk(source, followlinks=False):
            root_path = Path(root)

            # Security check: ensure we haven't escaped the source directory
            try:
                root_resolved = root_path.resolve()
                root_resolved.relative_to(source_resolved)
            except ValueError:
                # Path has escaped source directory, skip it
                continue

            # Add only regular files (exclude symlinks for security)
            for file_name in files:
                file_path = root_path / file_name
                if not file_path.is_symlink() and file_path.is_file():
                    zf.write(
                        file_path, arcname=file_path.relative_to(source).as_posix()
                    )
    return dest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def package():
    """Package one or more sources into release archives.

    Creates one archive per ``DEFAULT_FORMATS``.  The output file names are
    formed as ``<name><postfix?><extension>`` (e.g. ``mylib-v1.0.tar.gz``,
    ``mylib-v1.0.zip``, ``mytool-v0.5-bin.tar.bz``).

    May release multiple archives if ``multi_release_mode: true`` is specified in nanvix.toml.
    By default releases only one, containing the full contents of ``paths.release_dir()``

    Raises:
        SystemExit:
            - With :data:`~nanvix_zutil.exitcodes.EXIT_GENERAL_ERROR`
                if ``paths.release_dir()`` does not exist, if staging or copying
                fails, or if archive creation fails.
            - with :data:`~nanvix_zutil.exitcodes.EXIT_MISSING_DEP` if the
                release source directory is missing or not a directory.
    """
    formats = DEFAULT_FORMATS
    manifest = load_manifest()
    name = manifest.name
    multi_release = manifest.multi_release_mode
    sources = paths.release_dir()
    if sources.is_dir() and not sources.is_symlink():
        if multi_release:
            _sources: list[Path] = []
            for s in sources.iterdir():
                if s.is_dir() and not s.is_symlink():
                    _sources.append(s)
                else:
                    log.warning(f"Skipping non-directory item {s}")
            sources = _sources
            if len(sources) == 0:
                log.fatal(
                    "No release content found.",
                    code=EXIT_MISSING_DEP,
                    hint="Run ./z build first.",
                )
        else:
            sources = [sources]
    else:
        log.fatal(
            f"Release source {sources} is not a directory.",
            code=EXIT_MISSING_DEP,
            hint="Run ./z build first.",
        )

    dest = paths.dist_dir()

    try:
        rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        log.fatal(
            f"Cannot create destination directory '{dest}': {e}",
            code=EXIT_GENERAL_ERROR,
            hint="Check parent directory permissions and available disk space.",
        )

    for subdir in sources:
        for fmt in formats:
            postfix = f"-{subdir.name}" if multi_release else ""
            out = dest / f"{name}{postfix}{fmt.extension}"
            try:
                match fmt:
                    case ArchiveFormat.TAR_GZ:
                        _build_tarball(subdir, out, "gz")
                    case ArchiveFormat.TAR_BZ2:
                        _build_tarball(subdir, out, "bz2")
                    case ArchiveFormat.ZIP:
                        _build_zip(subdir, out)
            except Exception as e:
                log.fatal(
                    f"Failed to create {fmt.name} archive: {e}",
                    code=EXIT_GENERAL_ERROR,
                    hint="Check source directory access, disk space, and file permissions.",
                )

            # Verify the archive was actually created before logging success
            if not out.exists():
                log.fatal(
                    f"Failed to create archive: {out}",
                    code=EXIT_GENERAL_ERROR,
                    hint="Check disk space and permissions.",
                )

            log.info(f"Created {out}")
