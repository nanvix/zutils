# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.script (ZScript)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import nanvix_zutil.log as log_mod
from nanvix_zutil.script import ZScript


class TestZScriptInit(unittest.TestCase):
    """ZScript initialises correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_repo_root_resolved(self) -> None:
        repo_root = Path(self._tmpdir.name)
        script = ZScript(repo_root)
        self.assertEqual(script.repo_root, repo_root.resolve())

    def test_nanvix_dir(self) -> None:
        repo_root = Path(self._tmpdir.name)
        script = ZScript(repo_root)
        self.assertEqual(script.nanvix_dir, repo_root.resolve() / ".nanvix")

    def test_config_accessible(self) -> None:
        repo_root = Path(self._tmpdir.name)
        script = ZScript(repo_root)
        self.assertEqual(script.config.machine, "hyperlight")


class TestZScriptLifecycleHooks(unittest.TestCase):
    """Default lifecycle hooks are no-ops."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_script(self) -> ZScript:
        return ZScript(Path(self._tmpdir.name))

    def test_setup_noop(self) -> None:
        self._make_script().setup()

    def test_build_noop(self) -> None:
        self._make_script().build()

    def test_test_noop(self) -> None:
        self._make_script().test()

    def test_benchmark_noop(self) -> None:
        self._make_script().benchmark()

    def test_release_noop(self) -> None:
        self._make_script().release()

    def test_clean_noop(self) -> None:
        self._make_script().clean()


class TestZScriptRun(unittest.TestCase):
    """ZScript.run() executes subprocesses correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_run_success(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        result = script.run(sys.executable, "-c", "print('ok')")
        self.assertEqual(result.returncode, 0)

    def test_run_failure_exits(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                script.run(sys.executable, "-c", "raise SystemExit(1)")
            self.assertEqual(ctx.exception.code, 5)
        finally:
            log_mod.set_json_mode(False)


class TestZScriptSysrootRequiredFiles(unittest.TestCase):
    """sysroot_required_files() varies by deployment mode."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_multi_process_includes_linuxd_and_uservm(self) -> None:
        os.environ["NANVIX_DEPLOYMENT_MODE"] = "multi-process"
        script = ZScript(Path(self._tmpdir.name))
        files = script.sysroot_required_files()
        self.assertIn("bin/linuxd.elf", files)
        self.assertIn("bin/uservm.elf", files)

    def test_single_process_excludes_linuxd_and_uservm(self) -> None:
        os.environ["NANVIX_DEPLOYMENT_MODE"] = "single-process"
        script = ZScript(Path(self._tmpdir.name))
        files = script.sysroot_required_files()
        self.assertNotIn("bin/linuxd.elf", files)
        self.assertNotIn("bin/uservm.elf", files)

    def test_standalone_excludes_linuxd_and_uservm(self) -> None:
        os.environ["NANVIX_DEPLOYMENT_MODE"] = "standalone"
        script = ZScript(Path(self._tmpdir.name))
        files = script.sysroot_required_files()
        self.assertNotIn("bin/linuxd.elf", files)
        self.assertNotIn("bin/uservm.elf", files)

    def test_base_files_always_present(self) -> None:
        """Core files are required regardless of deployment mode."""
        for mode in ("multi-process", "single-process", "standalone"):
            os.environ["NANVIX_DEPLOYMENT_MODE"] = mode
            script = ZScript(Path(self._tmpdir.name))
            files = script.sysroot_required_files()
            self.assertIn("lib/libposix.a", files, f"missing in {mode}")
            self.assertIn("lib/user.ld", files, f"missing in {mode}")
            self.assertIn("bin/nanvixd.elf", files, f"missing in {mode}")
            self.assertIn("bin/kernel.elf", files, f"missing in {mode}")
            self.assertIn("bin/mkramfs.elf", files, f"missing in {mode}")

    def test_default_deployment_mode_is_multi_process(self) -> None:
        """Default (no env override) should be multi-process."""
        script = ZScript(Path(self._tmpdir.name))
        files = script.sysroot_required_files()
        self.assertIn("bin/linuxd.elf", files)
        self.assertIn("bin/uservm.elf", files)


if __name__ == "__main__":
    unittest.main()
