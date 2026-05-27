# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# pyright: reportPrivateUsage=false
"""Tests for nanvix_zutil.distclean_cmd."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nanvix_zutil.distclean_cmd import _run_consumer_clean, distclean, main

from tests.testutils import write_manifest


def _patch_prefix(nanvix_dir: Path):
    """Patch ``sys.prefix`` so distclean helpers resolve *nanvix_dir*.

    ``distclean()`` and ``_run_consumer_clean()`` compute
    ``nanvix_dir = Path(sys.prefix).parent`` on the assumption that the
    active venv lives at ``<nanvix-dir>/venv``.  Tests fake this by
    pointing ``sys.prefix`` at ``<nanvix-dir>/venv`` (the path need not
    exist on disk).
    """
    return patch("nanvix_zutil.distclean_cmd.sys.prefix", str(nanvix_dir / "venv"))


class TestDistcleanFunction(unittest.TestCase):
    """Behaviour of the standalone ``distclean()`` helper."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        self.nanvix_dir.mkdir()
        (self.nanvix_dir / "nanvix.toml").write_text("")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_removes_sysroot(self) -> None:
        sysroot = self.nanvix_dir / "sysroot"
        sysroot.mkdir()
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(sysroot.exists())

    def test_removes_buildroot(self) -> None:
        buildroot = self.nanvix_dir / "buildroot"
        buildroot.mkdir()
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(buildroot.exists())

    def test_removes_cache(self) -> None:
        cache = self.nanvix_dir / "cache"
        cache.mkdir()
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(cache.exists())

    def test_preserves_manifest(self) -> None:
        manifest = self.nanvix_dir / "nanvix.toml"
        self.assertTrue(manifest.exists())
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertTrue(manifest.exists())

    def test_removes_env_json(self) -> None:
        cfg = self.nanvix_dir / "env.json"
        cfg.write_text("{}")
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(cfg.exists())

    def test_removes_venv(self) -> None:
        venv = self.nanvix_dir / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin")
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(venv.exists())

    def test_removes_pycache(self) -> None:
        pycache = self.nanvix_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "z.cpython-312.pyc").write_bytes(b"\x00")
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(pycache.exists())

    def test_noop_when_nothing_exists(self) -> None:
        """distclean does not raise when artifact dirs are absent."""
        with _patch_prefix(self.nanvix_dir):
            distclean()

    def test_removes_file_artifact(self) -> None:
        """A regular file at an artifact path is unlinked."""
        sysroot_file = self.nanvix_dir / "sysroot"
        sysroot_file.write_text("not a directory")
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(sysroot_file.exists())

    def test_removes_symlink_artifact(self) -> None:
        """A symlink at an artifact path is unlinked."""
        target = self.nanvix_dir / "real_sysroot"
        target.mkdir()
        link = self.nanvix_dir / "sysroot"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported on this platform")
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(link.exists())

    def test_removes_broken_symlink(self) -> None:
        """A dangling symlink is unlinked."""
        target = self.nanvix_dir / "real_sysroot"
        target.mkdir()
        link = self.nanvix_dir / "sysroot"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported on this platform")
        target.rmdir()
        with _patch_prefix(self.nanvix_dir):
            distclean()
        self.assertFalse(link.is_symlink())

    def test_continues_on_permission_error(self) -> None:
        """distclean warns and skips artifacts it cannot remove."""
        venv = self.nanvix_dir / "venv"
        venv.mkdir()
        cache = self.nanvix_dir / "cache"
        cache.mkdir()

        original_rmtree = __import__("shutil").rmtree

        def _rmtree_fail_on_venv(path: object, *args: object, **kwargs: object) -> None:
            if Path(str(path)).name == "venv":
                raise PermissionError("locked by running process")
            original_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

        with (
            _patch_prefix(self.nanvix_dir),
            patch("shutil.rmtree", side_effect=_rmtree_fail_on_venv),
        ):
            distclean()

        self.assertTrue(venv.exists())
        self.assertFalse(cache.exists())


class TestDistcleanMain(unittest.TestCase):
    """``main()`` parses args and exits with SUCCESS."""

    def test_main_no_artifacts_exits_zero(self) -> None:
        """Exits 0 when there is nothing to clean."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nanvix_dir = Path(tmpdir) / ".nanvix"
            nanvix_dir.mkdir()
            with (
                _patch_prefix(nanvix_dir),
                patch("sys.argv", ["nanvix-zutil distclean"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            self.assertEqual(ctx.exception.code, 0)

    def test_main_removes_resolved_artifacts(self) -> None:
        """Artifacts under the dir resolved from ``sys.prefix`` are removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nanvix_dir = Path(tmpdir) / "custom-dir"
            nanvix_dir.mkdir()
            (nanvix_dir / "sysroot").mkdir()
            with (
                _patch_prefix(nanvix_dir),
                patch("sys.argv", ["nanvix-zutil distclean"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            self.assertEqual(ctx.exception.code, 0)
            self.assertFalse((nanvix_dir / "sysroot").exists())

    def test_main_rejects_unknown_args(self) -> None:
        """The parser no longer accepts ``--nanvix-dir`` (resolved via sys.prefix)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nanvix_dir = Path(tmpdir) / "custom-dir"
            nanvix_dir.mkdir()
            with (
                _patch_prefix(nanvix_dir),
                patch(
                    "sys.argv",
                    ["nanvix-zutil distclean", "--nanvix-dir", str(nanvix_dir)],
                ),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            # argparse exits 2 on unrecognised args
            self.assertEqual(ctx.exception.code, 2)


class TestRunConsumerClean(unittest.TestCase):
    """_run_consumer_clean() invokes the subclass clean() hook, best-effort."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.nanvix_dir = self.repo_root / ".nanvix"
        self.nanvix_dir.mkdir()
        write_manifest(self.repo_root)
        # Stamp file the consumer clean() can touch so we can assert it ran.
        self.stamp = self.repo_root / "clean.stamp"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write_z_py(self, body: str) -> None:
        """Write a .nanvix/z.py whose ZScript subclass body is *body*."""
        z_py = self.nanvix_dir / "z.py"
        z_py.write_text(
            "from nanvix_zutil.script import ZScript\n"
            "\n"
            "class MyZ(ZScript):\n"
            f"{body}\n"
        )

    def test_no_z_py_is_silent_noop(self) -> None:
        """When .nanvix/z.py is absent, the helper returns silently."""
        with (
            _patch_prefix(self.nanvix_dir),
            patch("nanvix_zutil.distclean_cmd.log") as mock_log,
        ):
            _run_consumer_clean()
        mock_log.warning.assert_not_called()
        mock_log.info.assert_not_called()

    def test_invokes_consumer_clean(self) -> None:
        """A subclass clean() override is invoked."""
        self._write_z_py(
            "    def clean(self) -> None:\n"
            f"        open(r'{self.stamp}', 'w').close()\n"
        )
        with _patch_prefix(self.nanvix_dir):
            _run_consumer_clean()
        self.assertTrue(self.stamp.exists(), "consumer clean() was not invoked")

    def test_clean_failure_is_swallowed(self) -> None:
        """clean() raising is logged and does not propagate."""
        self._write_z_py(
            "    def clean(self) -> None:\n" "        raise RuntimeError('boom')\n"
        )
        with (
            _patch_prefix(self.nanvix_dir),
            patch("nanvix_zutil.distclean_cmd.log") as mock_log,
        ):
            _run_consumer_clean()
        mock_log.warning.assert_called_once()
        self.assertIn("boom", mock_log.warning.call_args[0][0])

    def test_import_failure_is_swallowed(self) -> None:
        """A malformed z.py is logged and does not propagate."""
        (self.nanvix_dir / "z.py").write_text("this is not valid python(\n")
        with (
            _patch_prefix(self.nanvix_dir),
            patch("nanvix_zutil.distclean_cmd.log") as mock_log,
        ):
            _run_consumer_clean()
        mock_log.warning.assert_called_once()
        self.assertIn("Consumer clean() failed", mock_log.warning.call_args[0][0])

    def test_missing_subclass_is_swallowed(self) -> None:
        """A z.py with no ZScript subclass is logged and skipped."""
        (self.nanvix_dir / "z.py").write_text("x = 1\n")
        with (
            _patch_prefix(self.nanvix_dir),
            patch("nanvix_zutil.distclean_cmd.log") as mock_log,
        ):
            _run_consumer_clean()
        mock_log.warning.assert_called_once()
        self.assertIn("Consumer clean() failed", mock_log.warning.call_args[0][0])

    def test_missing_manifest_is_swallowed(self) -> None:
        """ZScript.__init__ failing (no manifest) is logged and skipped."""
        (self.nanvix_dir / "nanvix.toml").unlink()
        self._write_z_py("    def clean(self) -> None:\n" "        pass\n")
        with (
            _patch_prefix(self.nanvix_dir),
            patch("nanvix_zutil.distclean_cmd.log") as mock_log,
        ):
            _run_consumer_clean()
        mock_log.warning.assert_called_once()
        self.assertIn("clean() failed", mock_log.warning.call_args[0][0])


class TestMainIntegration(unittest.TestCase):
    """main() runs consumer clean() then removes artifacts in one call."""

    def test_main_calls_clean_then_distcleans(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            nanvix_dir = repo_root / ".nanvix"
            nanvix_dir.mkdir()
            write_manifest(repo_root)
            stamp = repo_root / "clean.stamp"
            sysroot = nanvix_dir / "sysroot"
            sysroot.mkdir()
            (nanvix_dir / "z.py").write_text(
                "from nanvix_zutil.script import ZScript\n"
                "\n"
                "class MyZ(ZScript):\n"
                "    def clean(self) -> None:\n"
                f"        open(r'{stamp}', 'w').close()\n"
            )

            with (
                _patch_prefix(nanvix_dir),
                patch("sys.argv", ["nanvix-zutil distclean"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            self.assertEqual(ctx.exception.code, 0)
            self.assertTrue(stamp.exists(), "consumer clean() was not invoked")
            self.assertFalse(sysroot.exists(), "sysroot was not removed")


if __name__ == "__main__":
    unittest.main()
