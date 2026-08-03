# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for the standalone ``nanvix-zutil release`` command."""

from __future__ import annotations

import sys
import unittest
from io import StringIO
from pathlib import Path

from nanvix_zutil import paths
from nanvix_zutil.commands import release as release_cmd
from nanvix_zutil.config import Config
from nanvix_zutil.exitcodes import EXIT_GENERAL_ERROR
from tests.testutils import write_manifest

_BASE = "test-linux-x86-microvm-standalone-256mb"


class _ReleaseTestBase(unittest.TestCase):
    def setUp(self) -> None:
        write_manifest()  # manifest name = "test"
        # Pin every config knob to env.json so archive names are deterministic
        # regardless of the host platform's defaults.
        cfg = Config()
        for key, val in {
            "NANVIX_HOST": "linux",
            "NANVIX_TARGET": "x86",
            "NANVIX_MACHINE": "microvm",
            "NANVIX_DEPLOYMENT_MODE": "standalone",
            "NANVIX_MEMORY_SIZE": "256mb",
        }.items():
            cfg.set(key, val)
        cfg.save()

    def _stage(self, subdir: str) -> Path:
        d = paths.staging_dir() / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / "artifact.bin").write_bytes(b"payload")
        return d


class TestReleaseMagicPaths(_ReleaseTestBase):
    """release/dev magic-path routing."""

    def test_release_only_produces_unsuffixed_archive(self) -> None:
        self._stage("regular")
        release_cmd.release()
        produced = {p.name for p in paths.dist_dir().iterdir()}
        self.assertEqual(produced, {f"{_BASE}.tar.gz"})

    def test_dev_only_produces_dev_archive(self) -> None:
        self._stage("dev")
        release_cmd.release()
        produced = {p.name for p in paths.dist_dir().iterdir()}
        self.assertEqual(produced, {f"{_BASE}-dev.tar.gz"})

    def test_both_produces_both_archives(self) -> None:
        self._stage("regular")
        self._stage("dev")
        release_cmd.release()
        produced = {p.name for p in paths.dist_dir().iterdir()}
        self.assertEqual(
            produced,
            {f"{_BASE}.tar.gz", f"{_BASE}-dev.tar.gz"},
        )

    def test_empty_release_is_ignored(self) -> None:
        (paths.staging_dir() / "regular").mkdir(parents=True)  # empty
        self._stage("dev")
        release_cmd.release()
        produced = {p.name for p in paths.dist_dir().iterdir()}
        self.assertEqual(produced, {f"{_BASE}-dev.tar.gz"})

    def test_neither_is_fatal(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            release_cmd.release()
        self.assertEqual(ctx.exception.code, EXIT_GENERAL_ERROR)

    def test_both_empty_is_fatal(self) -> None:
        (paths.staging_dir() / "regular").mkdir(parents=True)
        (paths.staging_dir() / "dev").mkdir(parents=True)
        with self.assertRaises(SystemExit) as ctx:
            release_cmd.release()
        self.assertEqual(ctx.exception.code, EXIT_GENERAL_ERROR)


class TestReleaseHostExtension(_ReleaseTestBase):
    """Extension is gated on Config.host."""

    def test_windows_uses_zip(self) -> None:
        cfg = Config()
        cfg.set("NANVIX_HOST", "windows")
        cfg.save()
        self._stage("regular")
        release_cmd.release()
        produced = {p.name for p in paths.dist_dir().iterdir()}
        self.assertEqual(
            produced,
            {"test-windows-x86-microvm-standalone-256mb.zip"},
        )


class TestReleaseFailureMessages(_ReleaseTestBase):
    """Failure paths surface actionable error messages."""

    def test_missing_staging_dir_emits_hint(self) -> None:
        buf = StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            with self.assertRaises(SystemExit):
                release_cmd.release()
        finally:
            sys.stderr = original_stderr

        output = buf.getvalue()
        self.assertIn("error:", output)
        self.assertIn("regular/", output)
        self.assertIn("dev/", output)
        self.assertIn("hint:", output)


if __name__ == "__main__":
    unittest.main()
