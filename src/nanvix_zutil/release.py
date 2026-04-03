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
from typing import Literal

from nanvix_zutil import log
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
    """
    mode: Literal["w:gz", "w:bz2"] = "w:gz" if compression == "gz" else "w:bz2"
    with tarfile.open(dest, mode) as tf:
        for child in sorted(source.rglob("*")):
            tf.add(child, arcname=child.relative_to(source))
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
            if child.is_file():
                zf.write(child, arcname=child.relative_to(source))
    return dest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def package(
    source: Path,
    dest: Path,
    name: str,
    formats: tuple[ArchiveFormat, ...] = DEFAULT_FORMATS,
) -> list[Path]:
    """Package *source* directory into release archives.

    Creates one archive per requested format.  The output file names are
    formed as ``<name><extension>`` (e.g. ``mylib-v1.0.tar.gz``,
    ``mylib-v1.0.zip``).

    Args:
        source: Directory to archive.  Must exist and be a directory.
        dest: Output directory for archives.  Created if it does not exist.
        name: Base name for the archives (without extension).
        formats: Archive formats to produce.  Defaults to
            :data:`DEFAULT_FORMATS` (tar.gz + zip).

    Returns:
        List of absolute paths to created archives, one per format, in
        the same order as *formats*.

    Raises:
        SystemExit: With :data:`~nanvix_zutil.exitcodes.EXIT_GENERAL_ERROR`
            if *source* does not exist or is not a directory.
    """
    if not source.is_dir():
        log.fatal(
            f"Release source '{source}' does not exist or is not a directory.",
            code=EXIT_GENERAL_ERROR,
            hint="Ensure the build step has run and produced output in the"
            " expected directory before calling 'release'.",
        )

    dest.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    for fmt in formats:
        out = dest / f"{name}{fmt.extension}"

        if fmt is ArchiveFormat.TAR_GZ:
            _build_tarball(source, out, "gz")
        elif fmt is ArchiveFormat.TAR_BZ2:
            _build_tarball(source, out, "bz2")
        elif fmt is ArchiveFormat.ZIP:
            _build_zip(source, out)

        log.info(f"Created {out}")
        created.append(out.resolve())

    log.success(f"Packaged {len(created)} archive(s) for '{name}' into {dest}")
    return created
