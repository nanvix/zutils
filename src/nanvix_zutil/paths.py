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


# -------------------------------
#   Paths
# -------------------------------
@functools.cache
def nanvix_root() -> Path:
    """Path to the ``.nanvix`` directory.

    Resolved by walking up the file tree from the current working
    directory.  Result is cached; call ``nanvix_root.cache_clear()`` to
    re-resolve (primarily for tests).
    """
    for p in (Path.cwd(), *Path.cwd().parents):
        candidate = p / ".nanvix"
        if candidate.is_dir():
            return candidate
    log.fatal("Could not find .nanvix directory.")


def manifest_path() -> Path:
    """Path to the cross-compilation manifest (``.nanvix/nanvix.toml``)."""
    return nanvix_root() / "nanvix.toml"


def repo_root() -> Path:
    """Path to the repository root (parent of ``.nanvix/``)."""
    return nanvix_root().parent


def out_dir() -> Path:
    """Path to the build output folder (``.nanvix/out``)."""
    return nanvix_root() / "out"


def build_out() -> Path:
    """Path to built outputs to be distributed with ``release``
    (``.nanvix/build``)."""
    return nanvix_root() / "build"


def dist_dir() -> Path:
    """Path to the release output folder (``.nanvix/dist``)."""
    return nanvix_root() / "dist"


def lib_out() -> Path:
    """Path to built library outputs to be bundled into the release
    lib path."""
    return build_out() / "lib"


def include_out() -> Path:
    """Path to built header outputs to be bundled into the release
    include path."""
    return build_out() / "include"


def bin_out() -> Path:
    """Path to built binaries to be bundled into the release bin path."""
    return build_out() / "bin"


def test_out() -> Path:
    """Path to built test binaries and ramfs images to be bundled for
    Windows tests."""
    return out_dir() / "test"


def buildroot() -> Path:
    """Path to the build root (``.nanvix/buildroot``).

    Used to store items needed at build time.
    """
    return nanvix_root() / "buildroot"


def sysroot() -> Path:
    """Path to the sysroot (``.nanvix/sysroot``).

    Used to store items needed at runtime.
    """
    return nanvix_root() / "sysroot"
