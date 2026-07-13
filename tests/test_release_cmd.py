# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for the standalone ``nanvix-zutil release`` command."""

from __future__ import annotations

import sys
import unittest
from io import StringIO
from pathlib import Path
from typing import override
from unittest.mock import patch

from nanvix_zutil import paths
from nanvix_zutil.commands import release as release_cmd
from nanvix_zutil.exitcodes import EXIT_GENERAL_ERROR
from nanvix_zutil.script import ZScript
from tests.testutils import write_manifest

# Pin every config-level knob so archive names are deterministic
# regardless of the developer's ambient environment.
_PINNED_ENV = {
    "NANVIX_HOST": "linux",
    "NANVIX_TARGET": "x86",
    "NANVIX_MACHINE": "microvm",
    "NANVIX_DEPLOYMENT_MODE": "standalone",
    "NANVIX_MEMORY_SIZE": "256mb",
}


class TestReleaseDefault(unittest.TestCase):
    """Default packaging path — no ``.nanvix/z.py`` override.

    Content coverage lives in ``tests/test_release.py``. These tests only
    verify wiring: the release directory is picked up, archives land in the
    dist directory under the manifest name, and a missing release directory
    fails cleanly.
    """

    def setUp(self) -> None:
        write_manifest()  # manifest name = "test"
        env_patch = patch.dict("os.environ", _PINNED_ENV, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def _populate_release_dir(self) -> Path:
        rel = paths.release_dir()
        rel.mkdir(parents=True, exist_ok=True)
        (rel / "artifact.bin").write_bytes(b"payload")
        return rel

    def test_packages_when_release_dir_exists(self) -> None:
        self._populate_release_dir()

        release_cmd.release()

        dist = paths.dist_dir()
        produced = {p.name for p in dist.iterdir()}
        self.assertEqual(
            produced,
            {
                "test-linux-x86-microvm-standalone-256mb.tar.gz",
                "test-linux-x86-microvm-standalone-256mb.zip",
            },
        )
        for p in dist.iterdir():
            self.assertGreater(p.stat().st_size, 0, f"empty archive: {p}")

    def test_emits_success_message(self) -> None:
        self._populate_release_dir()

        buf = StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            release_cmd.release()
        finally:
            sys.stderr = original_stderr

        output = buf.getvalue()
        self.assertIn("success:", output)
        self.assertIn(
            "Packaged 2 archive(s) for 'test-linux-x86-microvm-standalone-256mb'",
            output,
        )
        self.assertIn(str(paths.dist_dir()), output)

    def test_fails_when_release_dir_missing(self) -> None:
        self.assertFalse(paths.release_dir().exists())

        with self.assertRaises(SystemExit) as ctx:
            release_cmd.release()
        self.assertEqual(ctx.exception.code, EXIT_GENERAL_ERROR)

        dist = paths.dist_dir()
        if dist.exists():
            self.assertEqual(list(dist.iterdir()), [])

    def test_failure_emits_error_with_hint(self) -> None:
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
        self.assertIn(str(paths.release_dir()), output)
        self.assertIn("hint:", output)
        self.assertIn("release", output)


class TestReleaseTargetsOverride(unittest.TestCase):
    """A ``release_targets()`` override on ``.nanvix/z.py`` drives multi-archive packaging.

    We patch ``consumer_release_targets`` to simulate a consumer override
    without materialising a fake ``z.py`` module on disk.
    """

    def setUp(self) -> None:
        write_manifest()
        env_patch = patch.dict("os.environ", _PINNED_ENV, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def _populate(self, *subdirs: str) -> None:
        for sub in subdirs:
            d = paths.release_dir() / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / "artifact.bin").write_bytes(b"payload")

    def test_dispatches_one_package_call_per_target(self) -> None:
        self._populate("sysroot-pkg", "buildroot-pkg")

        targets = {
            "sysroot-pkg": "test-sysroot",
            "buildroot-pkg": "test-buildroot",
        }
        with (
            patch(
                "nanvix_zutil.commands.release.consumer_release_targets",
                return_value=targets,
            ),
            patch("nanvix_zutil.commands.release.package") as mock_pkg,
        ):
            release_cmd.release()

        rel = paths.release_dir()
        dist = paths.dist_dir()
        self.assertEqual(mock_pkg.call_count, 2)
        seen = {(tuple(c.args[0]), c.args[2]) for c in mock_pkg.call_args_list}
        self.assertEqual(
            seen,
            {
                ((rel / "sysroot-pkg",), "test-sysroot"),
                ((rel / "buildroot-pkg",), "test-buildroot"),
            },
        )
        for c in mock_pkg.call_args_list:
            self.assertEqual(c.args[1], dist)

    def test_empty_targets_falls_back_to_default(self) -> None:
        rel = paths.release_dir()
        rel.mkdir(parents=True, exist_ok=True)
        (rel / "artifact.bin").write_bytes(b"payload")

        with patch("nanvix_zutil.commands.release.package") as mock_pkg:
            release_cmd.release()

        mock_pkg.assert_called_once()
        args, _ = mock_pkg.call_args
        self.assertEqual(args[0], [rel])
        self.assertEqual(args[2], "test-linux-x86-microvm-standalone-256mb")


class TestConsumerReleaseTargets(unittest.TestCase):
    """``consumer_release_targets()`` picks up overrides from ``.nanvix/z.py``."""

    def setUp(self) -> None:
        write_manifest()

    def test_no_z_py_returns_empty(self) -> None:
        self.assertFalse((paths.nanvix_root() / "z.py").exists())
        self.assertEqual(release_cmd.consumer_release_targets(), {})

    def test_override_returned(self) -> None:
        class _Sub(ZScript):
            @override
            def release_targets(self) -> dict[str, str]:
                return {"sysroot-pkg": "test-sysroot"}

        # Create a placeholder z.py so the presence check passes; the
        # discover step is patched to return _Sub directly.
        (paths.nanvix_root() / "z.py").write_text("# placeholder\n")

        with patch("nanvix_zutil.__main__.discover_script_class", return_value=_Sub):
            self.assertEqual(
                release_cmd.consumer_release_targets(),
                {"sysroot-pkg": "test-sysroot"},
            )


if __name__ == "__main__":
    unittest.main()
