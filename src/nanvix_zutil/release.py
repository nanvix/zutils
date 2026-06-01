# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Release artifact packaging.

Produces release archives in multiple formats (``.tar.gz``, ``.tar.bz2``,
``.zip``) from a source directory.  Consumer repositories call
:func:`package` from their :meth:`~nanvix_zutil.ZScript.release` hook to
generate distribution archives::

    from nanvix_zutil.release import ArchiveFormat, package

    archives = package(
        name="mylib-microvm-standalone-256mb",
    )
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Literal, Sequence

from nanvix_zutil import log
from nanvix_zutil.constants import BUILD_OUT, DIST_DIR
from nanvix_zutil.exitcodes import EXIT_GENERAL_ERROR, EXIT_INVALID_ARGS

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


def _build_tarball(artifact_name: str, compression: Literal["gz", "bz2"]):
    """Create a tarball from *source* directory.

    All files are added relative to the archive root (i.e. paths inside the
    archive mirror the directory structure under *source*).

    Args:
        compression: Compression mode — ``"gz"`` for gzip or ``"bz2"`` for
            bzip2.

    Returns:
        *dest* after the tarball has been written.

    Note:
        Symlinks are excluded from the archive. Symlinked directories are not
        traversed.
    """
    mode: Literal["w:gz", "w:bz2"] = "w:gz" if compression == "gz" else "w:bz2"

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DIST_DIR / artifact_name
    with tarfile.open(out_path, mode) as tf:
        # Use os.walk with followlinks=False to prevent symlink directory traversal
        for root, dirs, files in os.walk(BUILD_OUT, followlinks=False):
            root_path = Path(root)

            # Security check: ensure we haven't escaped the source directory
            try:
                root_resolved = root_path.resolve()
                root_resolved.relative_to(BUILD_OUT)
            except ValueError:
                # Path has escaped source directory, skip it
                continue

            # Add regular files (exclude symlinks for security)
            for file_name in files:
                file_path = root_path / file_name
                if not file_path.is_symlink() and file_path.is_file():
                    tf.add(
                        file_path,
                        arcname=file_path.relative_to(BUILD_OUT).as_posix(),
                        recursive=False,
                    )

            # Add directories themselves (exclude symlinks for security)
            for dir_name in dirs:
                dir_path = root_path / dir_name
                if not dir_path.is_symlink() and dir_path.is_dir():
                    tf.add(
                        dir_path,
                        arcname=dir_path.relative_to(BUILD_OUT).as_posix(),
                        recursive=False,
                    )


def _build_zip(artifact_name: str):
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
    out_path = DIST_DIR / artifact_name
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Use os.walk with followlinks=False to prevent symlink directory traversal
        for root, _dirs, files in os.walk(BUILD_OUT, followlinks=False):
            root_path = Path(root)

            # Security check: ensure we haven't escaped the source directory
            try:
                root_resolved = root_path.resolve()
                root_resolved.relative_to(BUILD_OUT)
            except ValueError:
                # Path has escaped source directory, skip it
                continue

            # Add only regular files (exclude symlinks for security)
            for file_name in files:
                file_path = root_path / file_name
                if not file_path.is_symlink() and file_path.is_file():
                    zf.write(
                        file_path, arcname=file_path.relative_to(BUILD_OUT).as_posix()
                    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def package(
    name: str,
    formats: Sequence[ArchiveFormat] = DEFAULT_FORMATS,
) -> list[Path]:
    """Package one or more sources into release archives.

    Creates one archive per requested format.  The output file names are
    formed as ``<name><extension>`` (e.g. ``mylib-v1.0.tar.gz``,
    ``mylib-v1.0.zip``).

    Args:
        sources: Items to archive.  Each entry may be a file or a directory.
            Directory contents are merged flat into the staging root;
            individual files are placed at the staging root by their basename.
            Duplicate items are clobbered.
            Symlinks in *sources* and symlinks encountered inside source
            directories are silently skipped.
        dest: Output directory for archives.  Created if it does not exist.
        name: Base name for the archives (without extension). Must be a plain
            filename without path separators or parent directory traversal.
        formats: Archive formats to produce.  Defaults to
            :data:`DEFAULT_FORMATS` (tar.gz + zip).
        staging: Directory to use as the staging area.  When supplied, sources
            are copied directly into this directory (which must already exist)
            and the caller is responsible for its lifetime.  When omitted, a
            fresh temporary directory is created and removed automatically on
            return.

    Returns:
        List of absolute paths to created archives, one per format, in
        the same order as *formats*.

    Raises:
        SystemExit: With :data:`~nanvix_zutil.exitcodes.EXIT_GENERAL_ERROR`
            if any entry in *sources* does not exist, if staging or copying
            fails, or if archive creation fails.  With
            :data:`~nanvix_zutil.exitcodes.EXIT_INVALID_ARGS` if *name* is
            empty, whitespace-only, or contains path separators or parent
            directory traversal, or if an unknown format is encountered,
            or if *sources* is empty.
    """

    # Validate name is a safe, non-empty filename
    if not name or not name.strip():
        log.fatal(
            f"Invalid archive name '{name}': must be a non-empty filename",
            code=EXIT_INVALID_ARGS,
            hint="Provide a proper basename like 'mylib-v1.0'.",
        )

    # Validate name has no path separators or parent traversal
    if "/" in name or "\\" in name or ".." in name:
        log.fatal(
            f"Invalid archive name '{name}': must be a plain filename without path separators or '..'",
            code=EXIT_INVALID_ARGS,
            hint="Use a simple basename like 'mylib-v1.0' instead of paths like '../evil' or 'dir/name'.",
        )

    if formats is None:  # type: ignore[redundant-expr]
        log.fatal(
            "Invalid formats parameter: cannot be None",
            code=EXIT_INVALID_ARGS,
            hint="Provide a sequence of ArchiveFormat values, e.g., (ArchiveFormat.TAR_GZ, ArchiveFormat.ZIP).",
        )
    if isinstance(formats, (str, bytes)):
        log.fatal(
            f"Invalid formats parameter: {type(formats).__name__!r} is not a valid sequence",
            code=EXIT_INVALID_ARGS,
            hint="Provide a sequence of ArchiveFormat values, not a string or bytes object.",
        )
    if not isinstance(formats, Iterable):  # type: ignore[redundant-expr]
        log.fatal(
            f"Invalid formats parameter: {type(formats).__name__!r} is not iterable",
            code=EXIT_INVALID_ARGS,
            hint="Provide a sequence of ArchiveFormat values, e.g., (ArchiveFormat.TAR_GZ, ArchiveFormat.ZIP).",
        )

    # Create destination directory with proper error handling
    try:
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        # Verify it's actually a directory (not a file with the same name)
        if not DIST_DIR.is_dir():
            log.fatal(
                f"Destination path exists but is not a directory: {DIST_DIR}",
                code=EXIT_GENERAL_ERROR,
                hint="Choose a different destination path or remove the conflicting file.",
            )
    except (OSError, PermissionError) as e:
        log.fatal(
            f"Cannot create destination directory '{DIST_DIR}': {e}",
            code=EXIT_GENERAL_ERROR,
            hint="Check parent directory permissions and available disk space.",
        )

    def package_sources() -> list[Path]:
        created: list[Path] = []
        for fmt in formats:
            # Runtime validation: users could pass invalid types despite type hints
            if not isinstance(fmt, ArchiveFormat):  # type: ignore[redundant-expr]
                log.fatal(
                    f"Invalid archive format: {fmt!r} (expected ArchiveFormat)",
                    code=EXIT_INVALID_ARGS,
                    hint="Use one of the supported ArchiveFormat enum values (TAR_GZ, TAR_BZ2, ZIP).",
                )

            artifact_name = f"{name}{fmt.extension}"

            try:
                if fmt is ArchiveFormat.TAR_GZ:
                    _build_tarball(artifact_name, "gz")
                elif fmt is ArchiveFormat.TAR_BZ2:
                    _build_tarball(artifact_name, "bz2")
                elif fmt is ArchiveFormat.ZIP:
                    _build_zip(artifact_name)
                else:
                    # This should never happen with a proper ArchiveFormat enum value
                    log.fatal(
                        f"Unknown archive format: {fmt}",
                        code=EXIT_INVALID_ARGS,
                        hint="Use one of the supported ArchiveFormat enum values.",
                    )
            except Exception as e:
                log.fatal(
                    f"Failed to create {fmt.name} archive: {e}",
                    code=EXIT_GENERAL_ERROR,
                    hint="Check source directory access, disk space, and file permissions.",
                )

            # Verify the archive was actually created before logging success
            if not (DIST_DIR / artifact_name).exists():
                log.fatal(
                    f"Failed to create archive: {artifact_name}",
                    code=EXIT_GENERAL_ERROR,
                    hint="Check disk space and permissions.",
                )

            log.info(f"Created {artifact_name}")
            created.append(DIST_DIR / artifact_name)

        log.success(f"Packaged {len(created)} archive(s) for '{name}' into {DIST_DIR}")
        return created

    return package_sources()
