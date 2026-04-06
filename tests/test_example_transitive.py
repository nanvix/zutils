# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for the hello-transitive example.

Exercises transitive dependency resolution with mocked GitHub API
responses.  Verifies that ``./z lock`` produces a lockfile containing
both the direct dependency (``libfoo``) and the transitive dependency
(``zlib``) discovered from ``libfoo``'s shallow ``nanvix.lock`` release
asset.

CLI flag tests (--help, --json) invoke the real bootstrap wrappers
and may create a virtual environment with an editable install, which
can require network access to fetch build requirements.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.buildroot import Dependency, Ref, RefKind
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedPackage,
    read_lockfile,
    write_lockfile,
)
from nanvix_zutil.manifest import BuildMatrix, Manifest
from nanvix_zutil.resolver import resolve

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "hello-transitive"
_LIFECYCLE_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_release(
    tag: str = "v0.1.0",
    commitish: str = "aaa111",
    release_id: int = 100,
    assets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build a fake GitHub release dict."""
    if assets is None:
        assets = []
    return {
        "tag_name": tag,
        "target_commitish": commitish,
        "id": release_id,
        "assets": assets,
    }


def _tar_asset(name: str) -> dict[str, object]:
    """Build a fake .tar.bz2 asset entry."""
    return {
        "name": name,
        "browser_download_url": f"https://example.com/{name}",
    }


def _lockfile_asset() -> dict[str, object]:
    """Build a fake nanvix.lock asset entry."""
    return {
        "name": "nanvix.lock",
        "browser_download_url": "https://example.com/nanvix.lock",
    }


# ---------------------------------------------------------------------------
# Transitive resolution tests (mocked GitHub API)
# ---------------------------------------------------------------------------


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestTransitiveResolution(unittest.TestCase):
    """Verify that resolving hello-transitive's manifest discovers zlib."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        # Write a manifest matching the hello-transitive example
        self._manifest_path.write_text(
            '[package]\nname = "hello-transitive"\nversion = "0.1.0"\n'
            'nanvix-version = "0.12.267"\n'
            "\n[dependencies]\n"
            'libfoo = { commitish = "abc1234" }\n'
            "\n[system-dependencies]\n"
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def _resolve_libfoo_zlib(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> Lockfile:
        """Wire mocks for the standard libfoo→zlib scenario and resolve."""
        sysroot_release = _make_release(
            tag="v0.12.267",
            commitish="sysroot_sha",
            release_id=100,
            assets=[
                _tar_asset(
                    "nanvix-hyperlight-multi-process-release-128mb-sysroot_sha.tar.bz2"
                )
            ],
        )
        libfoo_release = _make_release(
            tag="libfoo-abc1234",
            commitish="abc1234full",
            release_id=200,
            assets=[
                _tar_asset("libfoo-hyperlight-multi-process-128mb.tar.bz2"),
                _lockfile_asset(),
            ],
        )
        zlib_release = _make_release(
            tag="zlib-v2.0.0",
            commitish="zlib_sha_full",
            release_id=300,
            assets=[_tar_asset("zlib-hyperlight-multi-process-128mb.tar.bz2")],
        )

        mock_resolve.side_effect = [sysroot_release, libfoo_release, zlib_release]

        libfoo_inner_lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:libfoo_inner",
                nanvix_zutil_version="0.2.2",
            ),
            builds=BuildMatrix(
                dimensions={
                    "platforms": ["hyperlight"],
                    "modes": ["multi-process"],
                    "memory": ["128mb"],
                },
                exclude=[],
            ),
            packages=[
                ResolvedPackage(
                    name="zlib",
                    repo="nanvix/zlib",
                    kind="dependency",
                    ref=Ref(kind=RefKind.TAG, value="zlib-v2.0.0"),
                    resolved_tag="zlib-v2.0.0",
                    resolved_commitish="zlib_sha_full",
                    release_id=300,
                ),
            ],
        )
        mock_download.side_effect = [libfoo_inner_lockfile, None]

        manifest = Manifest(
            name="hello-transitive",
            version="0.1.0",
            sysroot_ref=Ref(kind=RefKind.TAG, value="0.12.267"),
            builds=BuildMatrix(
                dimensions={
                    "platforms": ["hyperlight"],
                    "modes": ["multi-process"],
                    "memory": ["128mb"],
                },
                exclude=[],
            ),
            dependencies=[
                Dependency(
                    name="libfoo",
                    repo="nanvix/libfoo",
                    ref=Ref(kind=RefKind.COMMITISH, value="abc1234"),
                )
            ],
        )

        return resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

    def test_discovers_zlib_via_libfoo(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        """Resolver finds zlib transitively from libfoo's lockfile asset."""
        lockfile = self._resolve_libfoo_zlib(mock_resolve, mock_download)

        # Should have 3 packages: nanvix (sysroot), libfoo, zlib
        self.assertEqual(len(lockfile.packages), 3)
        names = [p.name for p in lockfile.packages]
        self.assertIn("nanvix", names)
        self.assertIn("libfoo", names)
        self.assertIn("zlib", names)

        # zlib should be marked as transitive, required by libfoo
        zlib_pkg = next(p for p in lockfile.packages if p.name == "zlib")
        self.assertTrue(zlib_pkg.transitive)
        self.assertIn("libfoo", zlib_pkg.required_by)

        # libfoo should list zlib in its dependencies
        libfoo_pkg = next(p for p in lockfile.packages if p.name == "libfoo")
        self.assertIn("zlib", libfoo_pkg.dependencies)
        self.assertFalse(libfoo_pkg.transitive)

    def test_lockfile_round_trip_preserves_transitive_info(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        """Write then read a lockfile with transitive deps; verify fidelity."""
        lockfile = self._resolve_libfoo_zlib(mock_resolve, mock_download)

        # Write and re-read
        lock_path = Path(self._tmpdir.name) / "nanvix.lock"
        write_lockfile(lockfile, lock_path)
        reloaded = read_lockfile(lock_path)

        self.assertEqual(len(reloaded.packages), len(lockfile.packages))

        for orig, loaded in zip(lockfile.packages, reloaded.packages):
            self.assertEqual(orig.name, loaded.name)
            self.assertEqual(orig.transitive, loaded.transitive)
            self.assertEqual(orig.required_by, loaded.required_by)
            self.assertEqual(orig.dependencies, loaded.dependencies)
            self.assertEqual(len(orig.assets), len(loaded.assets))


# ---------------------------------------------------------------------------
# CLI tests (using python -m nanvix_zutil)
# ---------------------------------------------------------------------------


class TestHelloTransitiveCli(unittest.TestCase):
    """End-to-end CLI tests via ``python -m nanvix_zutil``."""

    @staticmethod
    def _run_z(*args: str) -> subprocess.CompletedProcess[str]:
        """Run the example via ``python -m nanvix_zutil``."""
        return subprocess.run(
            [sys.executable, "-m", "nanvix_zutil", *args],
            cwd=str(_EXAMPLE_DIR),
            capture_output=True,
            text=True,
            timeout=_LIFECYCLE_TIMEOUT,
        )

    def test_help_returns_zero(self) -> None:
        """``--help`` exits successfully."""
        r = self._run_z("--help")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_args_shows_help(self) -> None:
        """No arguments prints help and exits 0."""
        r = self._run_z()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_json_mode(self) -> None:
        """``--json`` produces parseable JSON on stderr."""
        r = self._run_z("--json", "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        json_lines = [ln for ln in r.stderr.splitlines() if ln.startswith("{")]
        self.assertTrue(json_lines, "expected at least one JSON line on stderr")
        for line in json_lines:
            obj: object = json.loads(line)
            self.assertIsInstance(obj, dict)
            assert isinstance(obj, dict)
            typed = cast(dict[str, object], obj)
            self.assertIn("level", typed)


if __name__ == "__main__":
    unittest.main()
