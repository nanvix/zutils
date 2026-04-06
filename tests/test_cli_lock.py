# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for the ``lock`` CLI subcommand integration."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.buildroot import Ref, RefKind
from nanvix_zutil.cli import build_parser
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedAsset,
    ResolvedPackage,
    write_lockfile,
)
from nanvix_zutil.manifest import BuildMatrix
from nanvix_zutil.script import ZScript
from tests.testutils import write_manifest


def _make_mock_lockfile(manifest_path: Path) -> Lockfile:
    """Create a mock lockfile with matching manifest hash."""
    from nanvix_zutil.lockfile import compute_manifest_hash

    return Lockfile(
        metadata=LockfileMetadata(
            manifest_hash=compute_manifest_hash(manifest_path),
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
                name="nanvix",
                repo="nanvix/nanvix",
                kind="sysroot",
                ref=Ref(kind=RefKind.TAG, value="0.1.0"),
                resolved_tag="v0.1.0",
                resolved_commitish="aaa111",
                release_id=1,
                assets=[
                    ResolvedAsset(
                        name="nanvix-hyperlight-multi-process-release-128mb-aaa111.tar.bz2",
                        url="https://example.com/sysroot.tar.bz2",
                    ),
                ],
            ),
        ],
    )


class TestCliLockFlags(unittest.TestCase):
    """Parser accepts --check and --shallow flags for the lock subcommand."""

    def test_lock_subcommand_registered(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["lock"])
        self.assertEqual(args.subcommand, "lock")

    def test_lock_check_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["lock", "--check"])
        self.assertTrue(args.check)

    def test_lock_shallow_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["lock", "--shallow"])
        self.assertTrue(args.shallow)

    def test_lock_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["lock"])
        self.assertFalse(args.check)
        self.assertFalse(args.shallow)

    def test_help_includes_lock(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("lock", help_text)


class TestLockMethod(unittest.TestCase):
    """ZScript.lock() resolves deps and writes a lockfile."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))
        log_mod.set_json_mode(True)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    @patch("nanvix_zutil.script.resolve")
    def test_lock_writes_lockfile(self, mock_resolve: MagicMock) -> None:
        repo_root = Path(self._tmpdir.name)
        manifest_path = repo_root / ".nanvix" / "nanvix.toml"
        mock_resolve.return_value = _make_mock_lockfile(manifest_path)

        script = ZScript(repo_root)
        script.lock()

        lock_path = repo_root / ".nanvix" / "nanvix.lock"
        self.assertTrue(lock_path.is_file())
        mock_resolve.assert_called_once()

    @patch("nanvix_zutil.script.resolve")
    def test_lock_shallow(self, mock_resolve: MagicMock) -> None:
        repo_root = Path(self._tmpdir.name)
        manifest_path = repo_root / ".nanvix" / "nanvix.toml"
        mock_resolve.return_value = _make_mock_lockfile(manifest_path)

        script = ZScript(repo_root)
        script.lock(shallow=True)

        _, kwargs = mock_resolve.call_args
        self.assertTrue(kwargs.get("shallow"))


class TestLockCheck(unittest.TestCase):
    """ZScript.lock_check() verifies lockfile freshness."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))
        log_mod.set_json_mode(True)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_lock_check_exits_0_when_fresh(self) -> None:
        repo_root = Path(self._tmpdir.name)
        manifest_path = repo_root / ".nanvix" / "nanvix.toml"
        lock_path = repo_root / ".nanvix" / "nanvix.lock"

        lockfile = _make_mock_lockfile(manifest_path)
        write_lockfile(lockfile, lock_path)

        script = ZScript(repo_root)
        # Should not raise
        script.lock_check()

    def test_lock_check_exits_2_when_stale(self) -> None:
        repo_root = Path(self._tmpdir.name)
        manifest_path = repo_root / ".nanvix" / "nanvix.toml"
        lock_path = repo_root / ".nanvix" / "nanvix.lock"

        lockfile = _make_mock_lockfile(manifest_path)
        write_lockfile(lockfile, lock_path)

        # Modify the manifest to make the lockfile stale
        manifest_path.write_text(
            "[package]\n"
            'name = "test"\n'
            'version = "0.2.0"\n'
            'nanvix-version = "0.2.0"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
        )

        script = ZScript(repo_root)
        with self.assertRaises(SystemExit) as ctx:
            script.lock_check()
        self.assertEqual(ctx.exception.code, 2)

    def test_lock_check_exits_3_when_missing(self) -> None:
        repo_root = Path(self._tmpdir.name)

        script = ZScript(repo_root)
        with self.assertRaises(SystemExit) as ctx:
            script.lock_check()
        self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
