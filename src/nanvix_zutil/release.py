# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Release artifact packaging.

Produces release archives in multiple formats (``.tar.gz``, ``.tar.bz2``,
``.zip``) from a source directory.  Consumer repositories call
:func:`package` from their :meth:`~nanvix_zutil.ZScript.release` hook to
generate distribution archives::

    from nanvix_zutil.release import ArchiveFormat, package

    archives = package(
        source=Path("build/output"),
        dest=Path("dist"),
        name="mylib-hyperlight-multi-process-128mb",
    )
"""

from __future__ import annotations

import tarfile
import zipfile
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Sequence

from nanvix_zutil import log
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
    """
    mode: Literal["w:gz", "w:bz2"] = "w:gz" if compression == "gz" else "w:bz2"
    with tarfile.open(dest, mode) as tf:
        for child in sorted(source.rglob("*")):
            # Only include regular files and directories, reject symlinks and special files
            if not child.is_symlink() and (child.is_file() or child.is_dir()):
                tf.add(
                    child,
                    arcname=child.relative_to(source).as_posix(),
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
    """
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in sorted(source.rglob("*")):
            # Only include regular files, reject symlinks and special files for security
            if child.is_file() and not child.is_symlink():
                zf.write(child, arcname=child.relative_to(source).as_posix())
    return dest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def package(
    source: Path,
    dest: Path,
    name: str,
    formats: Sequence[Any] = DEFAULT_FORMATS,  # Any needed for runtime validation
) -> list[Path]:
    """Package *source* directory into release archives.

    Creates one archive per requested format.  The output file names are
    formed as ``<name><extension>`` (e.g. ``mylib-v1.0.tar.gz``,
    ``mylib-v1.0.zip``).

    Args:
        source: Directory to archive.  Must exist and be a directory.
        dest: Output directory for archives.  Created if it does not exist.
        name: Base name for the archives (without extension). Must be a plain
            filename without path separators or parent directory traversal.
        formats: Archive formats to produce.  Defaults to
            :data:`DEFAULT_FORMATS` (tar.gz + zip).

    Returns:
        List of absolute paths to created archives, one per format, in
        the same order as *formats*.

    Raises:
        SystemExit: With :data:`~nanvix_zutil.exitcodes.EXIT_GENERAL_ERROR`
            if *source* does not exist or is not a directory, or if archive
            creation fails. With :data:`~nanvix_zutil.exitcodes.EXIT_INVALID_ARGS`
            if *name* is empty, whitespace-only, or contains path separators
            or parent directory traversal, or if an unknown format is encountered.
    """
    if not source.is_dir():
        log.fatal(
            f"Release source '{source}' does not exist or is not a directory.",
            code=EXIT_GENERAL_ERROR,
            hint="Ensure the build step has run and produced output in the"
            " expected directory before calling 'release'.",
        )

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

    # Create destination directory with proper error handling
    try:
        dest.mkdir(parents=True, exist_ok=True)
        # Verify it's actually a directory (not a file with the same name)
        if not dest.is_dir():
            log.fatal(
                f"Destination path exists but is not a directory: {dest}",
                code=EXIT_GENERAL_ERROR,
                hint="Choose a different destination path or remove the conflicting file.",
            )
    except (OSError, PermissionError) as e:
        log.fatal(
            f"Cannot create destination directory '{dest}': {e}",
            code=EXIT_GENERAL_ERROR,
            hint="Check parent directory permissions and available disk space.",
        )
    created: list[Path] = []

    for fmt in formats:
        # Runtime validation: users could pass invalid types despite type hints
        if not isinstance(fmt, ArchiveFormat):
            log.fatal(
                f"Invalid archive format: {fmt!r} (expected ArchiveFormat)",
                code=EXIT_INVALID_ARGS,
                hint="Use one of the supported ArchiveFormat enum values (TAR_GZ, TAR_BZ2, ZIP).",
            )

        out = dest / f"{name}{fmt.extension}"

        try:
            if fmt is ArchiveFormat.TAR_GZ:
                _build_tarball(source, out, "gz")
            elif fmt is ArchiveFormat.TAR_BZ2:
                _build_tarball(source, out, "bz2")
            elif fmt is ArchiveFormat.ZIP:
                _build_zip(source, out)
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
        if not out.exists():
            log.fatal(
                f"Failed to create archive: {out}",
                code=EXIT_GENERAL_ERROR,
                hint="Check disk space and permissions.",
            )

        log.info(f"Created {out}")
        created.append(out.resolve())

    log.success(f"Packaged {len(created)} archive(s) for '{name}' into {dest}")
    return created
