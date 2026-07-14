# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Lazily-resolved path constants for the Nanvix tooling.

All paths are exposed as zero-arg functions so that resolution is
deferred until first use.  This keeps ``import nanvix_zutil`` safe to
run from a directory that does not contain a ``.nanvix/`` folder (e.g.
under pytest collection from the repo root) and lets tests redirect
every derived path by ``chdir``-ing and calling
``nanvix_root.cache_clear()`` from a single fixture, instead of
monkey-patching one symbol per consumer module.
"""

import functools
from pathlib import Path

import nanvix_zutil.log as log
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP


# -------------------------------
#   Paths
# -------------------------------
@functools.cache
def nanvix_root() -> Path:
    """Path to the ``.nanvix`` directory.

    Resolved by walking up the file tree from the current working
    directory.  The returned path is canonicalised via ``Path.resolve``
    so that downstream comparisons are insensitive to platform path
    aliasing (notably Windows 8.3 short names such as ``RUNNER~1``
    versus their long-form equivalents).  Result is cached; call
    ``nanvix_root.cache_clear()`` to re-resolve (primarily for tests).
    """
    for p in (Path.cwd(), *Path.cwd().parents):
        candidate = p / ".nanvix"
        if candidate.is_dir():
            return candidate.resolve()
    log.fatal(
        "Could not find .nanvix directory.",
        code=EXIT_MISSING_DEP,
        hint="Run this command from a Nanvix consumer repo root.",
    )


def manifest_path() -> Path:
    """Path to the cross-compilation manifest (``.nanvix/nanvix.toml``)."""
    return nanvix_root() / "nanvix.toml"


def z_py_path() -> Path:
    """Path to the ZScript implementer module. (``.nanvix/z.py``)"""
    return nanvix_root() / "z.py"


def repo_root() -> Path:
    """Path to the repository root (parent of ``.nanvix/``)."""
    return nanvix_root().parent


def out_dir() -> Path:
    """Path to the build output folder (``.nanvix/out``)."""
    return nanvix_root() / "out"


def staging_dir() -> Path:
    """Path to built outputs staged for the ``release`` command
    (``.nanvix/out/staging``)."""
    return out_dir() / "staging"


def dist_dir() -> Path:
    """Path to the release output folder (``.nanvix/out/dist``)."""
    return out_dir() / "dist"


def dev_out() -> Path:
    """Path to build outputs bundled into the ``-dev`` archive consumed
    by downstream builds (headers, static/shared libraries).
    (``.nanvix/out/staging/dev``)"""
    return staging_dir() / "dev"


def regular_out() -> Path:
    """Path to build outputs bundled into the end-user release archive
    (runtime binaries, initrd images). (``.nanvix/out/staging/regular``)"""
    return staging_dir() / "regular"


def test_out() -> Path:
    """Path to built test binaries and ramfs images to be bundled for
    tests. These are _excluded_ from releases. (``.nanvix/out/test``)"""
    return out_dir() / "test"


def buildroot() -> Path:
    """Path to the build root (``.nanvix/buildroot``).

    Used to store items needed at build time.
    """
    return nanvix_root() / "buildroot"


def sysroot() -> Path:
    """Path to the sysroot.

    Prefers the configured ``NANVIX_SYSROOT`` from ``env.json`` so
    downstreams that stage a shared sysroot outside ``.nanvix/sysroot``
    (e.g. Windows CI reusing another consumer's) resolve correctly.
    Falls back to ``.nanvix/sysroot`` when unset.
    """
    # Deferred import: ``config`` imports ``paths.nanvix_root``.
    from nanvix_zutil.config import CFG_SYSROOT, Config

    configured = Config().get(CFG_SYSROOT)
    return Path(configured) if configured else nanvix_root() / "sysroot"
