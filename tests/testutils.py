# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Shared test helpers for nanvix_zutil tests."""

import os
import tempfile
import unittest
from pathlib import Path

from nanvix_zutil.paths import nanvix_root

# Minimal valid manifest content.
MINIMAL_MANIFEST = (
    "[package]\n" 'name = "test"\n' 'version = "0.1.0"\n' 'nanvix-version = "0.1.0"\n'
)

# Manifest with one build-time dependency.
MANIFEST_WITH_DEPS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "0.1.0"\n'
    "\n"
    "[dependencies]\n"
    'zlib = "1.0"\n'
)

# Manifest with "latest" sysroot and a VERSION dep.
MANIFEST_LATEST_WITH_DEPS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "latest"\n'
    "\n"
    "[dependencies]\n"
    'zlib = "1.3.1"\n'
)


def make_toml(
    *,
    name: str = "myapp",
    version: str = "1.0.0",
    nanvix_version: str = "0.12.257",
    deps: dict[str, str] | None = None,
    sys_deps: dict[str, str] | None = None,
) -> str:
    """Build a nanvix.toml string with sensible defaults.

    Dependency values are raw TOML fragments placed after ``=``.
    For string values, include quotes: ``deps={"zlib": '"1.0.0"'}``.
    For inline tables: ``deps={"zlib": '{ commitish = "abc" }'}``.
    """
    lines = [
        "[package]",
        f'name = "{name}"',
        f'version = "{version}"',
        f'nanvix-version = "{nanvix_version}"',
    ]
    if deps is not None:
        lines.append("[dependencies]")
        for dep_name, dep_value in deps.items():
            lines.append(f"{dep_name} = {dep_value}")
    if sys_deps is not None:
        lines.append("[system-dependencies]")
        for dep_name, dep_value in sys_deps.items():
            lines.append(f"{dep_name} = {dep_value}")
    return "\n".join(lines) + "\n"


def write_manifest(content: str = MINIMAL_MANIFEST) -> None:
    """Create ``.nanvix/nanvix.toml`` under the current working directory.

    Callers are expected to have already pointed cwd at the consumer
    repo (typically via :func:`chdir_to`).
    """
    dest = Path.cwd() / ".nanvix" / "nanvix.toml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)


def chdir_to(
    testcase: unittest.TestCase,
    repo_root: Path | str | tempfile.TemporaryDirectory[str],
) -> None:
    """Point cwd at *repo_root* so .nanvix/ resolves there.

    When *repo_root* is a :class:`tempfile.TemporaryDirectory`, its
    ``cleanup()`` is registered via :meth:`unittest.TestCase.addCleanup`
    so it runs **after** the cwd has been restored.  Callers must then
    not also clean the tmpdir themselves in ``tearDown`` (doing so
    runs before ``addCleanup`` and re-introduces the Windows
    cwd-still-inside-tmpdir cleanup failure).

    Ensures ``<repo_root>/.nanvix/`` exists so subsequent calls that
    rely on :func:`nanvix_zutil.paths.nanvix_root` (which walks up
    looking for a ``.nanvix/`` marker) succeed even when the caller
    hasn't written any artifacts there yet.

    Saves the previous cwd and registers an ``addCleanup`` that
    restores it and clears the :func:`nanvix_root` cache.  Restoring
    cwd before tmpdir cleanup is required on Windows, which refuses
    to delete a directory that any process has as cwd.

    The restore tolerates the previous cwd having been removed by
    an earlier ``tearDown`` (common when callers nest ``chdir_to``
    inside a tmpdir whose cleanup runs in tearDown); in that case it
    falls back to the system temp directory.
    """
    if isinstance(repo_root, tempfile.TemporaryDirectory):
        tmpdir = repo_root
        target = Path(tmpdir.name)
        # Registered first => runs LAST (after the cwd restore below).
        testcase.addCleanup(tmpdir.cleanup)
    else:
        target = Path(repo_root)

    (target / ".nanvix").mkdir(exist_ok=True)
    orig_cwd = os.getcwd()
    os.chdir(target)
    nanvix_root.cache_clear()

    def _restore_cwd() -> None:
        try:
            os.chdir(orig_cwd)
        except FileNotFoundError:
            os.chdir(tempfile.gettempdir())

    testcase.addCleanup(nanvix_root.cache_clear)
    # Registered last => runs FIRST, before any earlier-registered cleanup
    # (notably the tmpdir cleanup above, which would otherwise fail on
    # Windows while cwd is still inside the tmpdir).
    testcase.addCleanup(_restore_cwd)
