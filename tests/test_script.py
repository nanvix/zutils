# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.script (ZScript)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.docker import (
    DEFAULT_DOCKER_IMAGE,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
)
from nanvix_zutil.script import ZScript
from tests.testutils import write_manifest


class TestZScriptInit(unittest.TestCase):
    """ZScript initialises correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))
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

    def test_manifest_loaded(self) -> None:
        repo_root = Path(self._tmpdir.name)
        script = ZScript(repo_root)
        self.assertEqual(script.manifest.sysroot_ref.value, "0.1.0")

    def test_missing_manifest_exits_3(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        repo_root = Path(tmpdir.name)
        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                ZScript(repo_root)
            self.assertEqual(ctx.exception.code, 3)
        finally:
            log_mod.set_json_mode(False)


class TestZScriptLifecycleHooks(unittest.TestCase):
    """Default lifecycle hooks are no-ops."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

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
        write_manifest(Path(self._tmpdir.name))

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
        write_manifest(Path(self._tmpdir.name))
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


class TestZScriptDockerIntegration(unittest.TestCase):
    """Tests for ZScript Docker mode."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_script(self) -> ZScript:
        return ZScript(Path(self._tmpdir.name))

    def test_docker_not_active_by_default(self) -> None:
        script = self._make_script()
        self.assertIsNone(script.docker)

    def test_docker_image_default(self) -> None:
        script = self._make_script()
        self.assertEqual(script.docker_image(), DEFAULT_DOCKER_IMAGE)

    def test_docker_config_returns_dockerconfig(self) -> None:
        script = self._make_script()
        cfg = script.docker_config("test-image")
        self.assertIsInstance(cfg, DockerConfig)
        self.assertEqual(cfg.image, "test-image")

    def test_docker_config_mounts_workspace(self) -> None:
        script = self._make_script()
        cfg = script.docker_config("test-image")
        workspace_mount = next(
            (m for m in cfg.mounts if m.container_path == WORKSPACE_CONTAINER_PATH),
            None,
        )
        self.assertIsNotNone(workspace_mount)
        assert workspace_mount is not None
        self.assertEqual(workspace_mount.host_path, script.repo_root)

    def test_translate_path_no_docker(self) -> None:
        """Without Docker, translate_path returns the input unchanged."""
        script = self._make_script()
        p = Path("/some/host/path")
        self.assertEqual(script.translate_path(p), p)

    def test_translate_path_with_docker(self) -> None:
        """With Docker active, translate_path translates via DockerConfig."""
        script = self._make_script()
        script.docker = DockerConfig(
            image="test-image",
            mounts=[
                Mount(
                    host_path=script.repo_root,
                    container_path=WORKSPACE_CONTAINER_PATH,
                )
            ],
        )
        result = script.translate_path(script.repo_root / "src" / "main.c")
        self.assertEqual(result, WORKSPACE_CONTAINER_PATH / "src" / "main.c")

    def test_run_without_docker_uses_args_directly(self) -> None:
        """Without Docker, run() executes the command as-is."""
        script = self._make_script()
        result = script.run(sys.executable, "-c", "print('ok')")
        self.assertEqual(result.returncode, 0)

    def test_run_with_docker_false_opt_out(self) -> None:
        """docker=False bypasses Docker even when _docker is set."""
        script = self._make_script()
        script.docker = DockerConfig(
            image="should-not-be-used",
            mounts=[],
        )
        # This should run on host, not via docker
        result = script.run(sys.executable, "-c", "print('ok')", docker=False)
        self.assertEqual(result.returncode, 0)

    def test_run_with_docker_wraps_command(self) -> None:
        """When Docker is active, run() prepends docker run to the command."""
        script = self._make_script()
        script.docker = DockerConfig(
            image="nanvix/toolchain:latest-minimal",
            mounts=[
                Mount(
                    host_path=script.repo_root,
                    container_path=WORKSPACE_CONTAINER_PATH,
                )
            ],
            uid=1000,
            gid=1000,
        )
        captured: list[list[str]] = []

        import subprocess as sp

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.run("make", "all")

        self.assertTrue(captured)
        self.assertIn("docker", captured[0])
        self.assertIn("make", captured[0])
        self.assertIn("all", captured[0])


if __name__ == "__main__":
    unittest.main()
