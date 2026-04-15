# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.script (ZScript)."""

import os
import subprocess as sp
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
    DEFAULT_DOCKER_IMAGE,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
)
from nanvix_zutil.script import ZScript
from tests.testutils import (
    MANIFEST_LATEST_WITH_DEPS,
    MANIFEST_WITH_DEPS,
    write_manifest,
)


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
        self.assertEqual(script.config.machine, "microvm")

    def test_manifest_loaded(self) -> None:
        repo_root = Path(self._tmpdir.name)
        script = ZScript(repo_root)
        self.assertEqual(script.manifest.sysroot_ref.value, "0.1.0")

    def test_sysroot_initially_none(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        self.assertIsNone(script.sysroot)

    def test_buildroot_initially_none(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        self.assertIsNone(script.buildroot)

    def test_log_attribute_is_log_module(self) -> None:
        """self.log refers to the nanvix_zutil.log module."""
        import nanvix_zutil.log as log_module

        script = ZScript(Path(self._tmpdir.name))
        self.assertIs(script.log, log_module)

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


class TestZScriptAutoSetup(unittest.TestCase):
    """Base setup() auto-downloads sysroot and dependencies."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_setup_downloads_sysroot(self) -> None:
        """setup() calls Sysroot.download with config values."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch(
            "nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot
        ) as mock_download:
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

            # Assert Sysroot.download was called once with the expected kwargs.
            mock_download.assert_called_once()
            _, kwargs = mock_download.call_args
            self.assertEqual(kwargs["machine"], script.config.machine)
            self.assertEqual(kwargs["deployment_mode"], script.config.deployment_mode)
            self.assertEqual(kwargs["memory_size"], script.config.memory_size)
            self.assertEqual(kwargs["tag"], script.manifest.sysroot_ref.value)
            self.assertIsInstance(kwargs["dest"], Path)
            self.assertTrue(str(kwargs["dest"]).startswith(str(script.nanvix_dir)))
            self.assertIs(kwargs["config"], script.config)
        fake_sysroot.verify.assert_called_once()
        self.assertIs(script.sysroot, fake_sysroot)

    def test_setup_with_deps_creates_buildroot(self) -> None:
        """setup() with manifest dependencies creates Buildroot and installs all deps."""
        write_manifest(Path(self._tmpdir.name), MANIFEST_WITH_DEPS)

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        fake_buildroot = MagicMock()
        fake_release: dict[str, object] = {"tag_name": "1.0-nanvix-0.1.0"}

        with (
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch(
                "nanvix_zutil.script.Buildroot.create", return_value=fake_buildroot
            ) as mock_create,
            patch(
                "nanvix_zutil.script.resolve_release_with_fallback",
                return_value=(fake_release, "0.1.0"),
            ),
        ):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        # Buildroot.create called once with the nanvix_dir/buildroot path.
        mock_create.assert_called_once()
        (create_path,), _ = mock_create.call_args
        self.assertEqual(create_path, script.nanvix_dir / "buildroot")

        # install_dep called once per dependency in the manifest.
        dep_count = len(script.manifest.dependencies)
        self.assertEqual(fake_buildroot.install_dep.call_count, dep_count)

        # buildroot attribute is set on the instance.
        self.assertIs(script.buildroot, fake_buildroot)

    def test_setup_no_deps_skips_buildroot(self) -> None:
        """setup() with no manifest dependencies leaves buildroot as None."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        self.assertIsNone(script.buildroot)

    def test_setup_saves_config(self) -> None:
        """setup() persists the sysroot path to env.json."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        config_file = Path(self._tmpdir.name) / ".nanvix" / "env.json"
        self.assertTrue(config_file.exists())


class TestZScriptSetupLatestSysroot(unittest.TestCase):
    """setup() with nanvix-version = "latest" suffixes deps correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name), MANIFEST_LATEST_WITH_DEPS)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_setup_latest_sysroot_suffixes_deps(self) -> None:
        """setup() suffixes VERSION deps with the resolved sysroot tag."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.tag = "v0.12.277"

        fake_buildroot = MagicMock()
        fake_release: dict[str, object] = {"tag_name": "1.3.1-nanvix-0.12.277"}

        with (
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch("nanvix_zutil.script.Buildroot.create", return_value=fake_buildroot),
            patch(
                "nanvix_zutil.script.resolve_release_with_fallback",
                return_value=(fake_release, "0.12.277"),
            ),
        ):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        # install_dep should be called with the suffixed ref value.
        fake_buildroot.install_dep.assert_called_once()
        _, kwargs = fake_buildroot.install_dep.call_args
        self.assertEqual(kwargs["dep"].ref.value, "1.3.1-nanvix-0.12.277")

    def test_setup_latest_sysroot_empty_tag_fatal(self) -> None:
        """setup() exits fatally when sysroot tag is empty (upgrade path)."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.tag = ""

        log_mod.set_json_mode(True)
        try:
            with (
                patch(
                    "nanvix_zutil.script.Sysroot.download",
                    return_value=fake_sysroot,
                ),
                self.assertRaises(SystemExit) as ctx,
            ):
                script = ZScript(Path(self._tmpdir.name))
                script.setup()

            self.assertEqual(ctx.exception.code, 3)
        finally:
            log_mod.set_json_mode(False)


class TestZScriptLifecycleHooks(unittest.TestCase):
    """Default consumer lifecycle hooks are no-ops."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_script(self) -> ZScript:
        return ZScript(Path(self._tmpdir.name))

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


class TestZScriptDistclean(unittest.TestCase):
    """distclean() removes transient .nanvix/ artifacts."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_script(self) -> ZScript:
        return ZScript(Path(self._tmpdir.name))

    def _nanvix(self) -> Path:
        return Path(self._tmpdir.name) / ".nanvix"

    def test_distclean_removes_sysroot(self) -> None:
        sysroot_dir = self._nanvix() / "sysroot"
        sysroot_dir.mkdir()
        self._make_script().distclean()
        self.assertFalse(sysroot_dir.exists())

    def test_distclean_removes_buildroot(self) -> None:
        buildroot_dir = self._nanvix() / "buildroot"
        buildroot_dir.mkdir()
        self._make_script().distclean()
        self.assertFalse(buildroot_dir.exists())

    def test_distclean_removes_cache(self) -> None:
        cache_dir = self._nanvix() / "cache"
        cache_dir.mkdir()
        self._make_script().distclean()
        self.assertFalse(cache_dir.exists())

    def test_distclean_removes_builds(self) -> None:
        builds_dir = self._nanvix() / "_builds"
        builds_dir.mkdir()
        (builds_dir / "microvm-standalone-256mb").mkdir()
        self._make_script().distclean()
        self.assertFalse(builds_dir.exists())

    def test_distclean_preserves_manifest(self) -> None:
        manifest = self._nanvix() / "nanvix.toml"
        self.assertTrue(manifest.exists())
        self._make_script().distclean()
        self.assertTrue(manifest.exists())

    def test_distclean_removes_config(self) -> None:
        config_file = self._nanvix() / "env.json"
        config_file.write_text("{}")
        self._make_script().distclean()
        self.assertFalse(config_file.exists())

    def test_distclean_removes_venv(self) -> None:
        venv_dir = self._nanvix() / "venv"
        venv_dir.mkdir()
        (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin")
        self._make_script().distclean()
        self.assertFalse(venv_dir.exists())

    def test_distclean_removes_pycache(self) -> None:
        pycache_dir = self._nanvix() / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "z.cpython-312.pyc").write_bytes(b"\x00")
        self._make_script().distclean()
        self.assertFalse(pycache_dir.exists())

    def test_distclean_noop_when_nothing_exists(self) -> None:
        """distclean() does not raise when artifact dirs are absent."""
        self._make_script().distclean()

    def test_distclean_removes_file_artifact(self) -> None:
        """distclean() removes a regular file if an artifact path is a file."""
        sysroot_file = self._nanvix() / "sysroot"
        sysroot_file.write_text("not a directory")
        self._make_script().distclean()
        self.assertFalse(sysroot_file.exists())

    def test_distclean_removes_symlink_artifact(self) -> None:
        """distclean() removes a symlink if an artifact path is a symlink."""
        target = self._nanvix() / "real_sysroot"
        target.mkdir()
        link = self._nanvix() / "sysroot"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported on this platform")
        self._make_script().distclean()
        self.assertFalse(link.exists())

    def test_distclean_removes_broken_symlink(self) -> None:
        """distclean() removes a symlink even when its target is gone."""
        target = self._nanvix() / "real_sysroot"
        target.mkdir()
        link = self._nanvix() / "sysroot"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported on this platform")
        target.rmdir()
        self._make_script().distclean()
        self.assertFalse(link.is_symlink())

    def test_distclean_continues_on_permission_error(self) -> None:
        """distclean() warns and skips artifacts it cannot remove."""
        venv_dir = self._nanvix() / "venv"
        venv_dir.mkdir()
        cache_dir = self._nanvix() / "cache"
        cache_dir.mkdir()

        original_rmtree = __import__("shutil").rmtree

        def _rmtree_fail_on_venv(path: object, *args: object, **kwargs: object) -> None:
            if Path(str(path)).name == "venv":
                raise PermissionError("locked by running process")
            original_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

        with patch("shutil.rmtree", side_effect=_rmtree_fail_on_venv):
            self._make_script().distclean()

        self.assertTrue(venv_dir.exists())
        self.assertFalse(cache_dir.exists())


class TestZScriptAvailableSubcommands(unittest.TestCase):
    """available_subcommands() reflects hook overrides."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_base_class_exposes_only_auto_hooks(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        available = script.available_subcommands()
        for hook in ZScript.AUTO_HOOKS:
            self.assertIn(hook, available, f"{hook!r} should always be available")
        for hook in ZScript.CONSUMER_HOOKS:
            self.assertNotIn(
                hook, available, f"{hook!r} should not appear when not overridden"
            )

    def test_subclass_exposes_overridden_hooks(self) -> None:
        class _Sub(ZScript):
            def build(self) -> None:
                pass

            def test(self) -> None:
                pass

        script = _Sub(Path(self._tmpdir.name))
        available = script.available_subcommands()
        self.assertIn("build", available)
        self.assertIn("test", available)

    def test_subclass_hides_non_overridden_hooks(self) -> None:
        class _Sub(ZScript):
            def build(self) -> None:
                pass

        script = _Sub(Path(self._tmpdir.name))
        available = script.available_subcommands()
        self.assertNotIn("clean", available)
        self.assertNotIn("benchmark", available)

    def test_all_hooks_overridden(self) -> None:
        class _FullSub(ZScript):
            def build(self) -> None:
                pass

            def test(self) -> None:
                pass

            def benchmark(self) -> None:
                pass

            def release(self) -> None:
                pass

            def clean(self) -> None:
                pass

        script = _FullSub(Path(self._tmpdir.name))
        available = script.available_subcommands()
        for hook in ZScript.CONSUMER_HOOKS:
            self.assertIn(hook, available)


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

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_run_kvm_fatal_on_windows(self, _mock: object) -> None:
        """run() with kvm=True exits fatally on Windows."""
        script = ZScript(Path(self._tmpdir.name))
        script.docker = DockerConfig(
            image="test-image",
            mounts=[],
            uid=0,
            gid=0,
        )
        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                script.run("echo", kvm=True)
            self.assertEqual(ctx.exception.code, 5)
        finally:
            log_mod.set_json_mode(False)

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_run_uses_windows_cmd_when_configured(self, _mock: object) -> None:
        """run() delegates to build_windows_run_cmd on Windows."""
        script = ZScript(Path(self._tmpdir.name))
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
            crlf_files=["Makefile"],
            output_files=["output.elf"],
        )
        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.run("make", "all")

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # Should use sh -c (Windows tar-copy mode).
        self.assertIn("sh", cmd)
        self.assertIn("-c", cmd)

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_run_dispatch_windows_default_docker(self, _mock: object) -> None:
        """run() uses build_windows_run_cmd on Windows even with default
        (empty) crlf_files and output_files — the dispatch no longer requires
        these fields to be populated."""
        script = ZScript(Path(self._tmpdir.name))
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
            # crlf_files and output_files intentionally left as defaults ([])
        )
        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.run("make", "all")

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # Should still use sh -c (Windows tar-copy mode) even without
        # crlf_files/output_files.
        self.assertIn("sh", cmd)
        self.assertIn("-c", cmd)

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_run_dispatch_linux_uses_build_run_cmd(self, *_mocks: object) -> None:
        """run() uses build_run_cmd on Linux (not the Windows tar-copy path)."""
        script = ZScript(Path(self._tmpdir.name))
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
        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.run("make", "all")

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # Standard docker run — should NOT use sh -c wrapping.
        self.assertEqual(cmd[:3], ["docker", "run", "--rm"])
        # Inner command is appended directly (not wrapped in sh -c).
        self.assertEqual(cmd[-2:], ["make", "all"])
        self.assertNotEqual(cmd[-3:-1], ["sh", "-c"])


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

    def test_default_deployment_mode_is_standalone(self) -> None:
        """Default (no env override) should be standalone."""
        script = ZScript(Path(self._tmpdir.name))
        files = script.sysroot_required_files()
        self.assertNotIn("bin/linuxd.elf", files)
        self.assertNotIn("bin/uservm.elf", files)


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

    def test_docker_config_no_buildroot_mount_when_absent(self) -> None:
        """No buildroot mount is added when the buildroot dir does not exist."""
        script = self._make_script()
        cfg = script.docker_config("test-image")
        buildroot_mount = next(
            (m for m in cfg.mounts if m.container_path == BUILDROOT_CONTAINER_PATH),
            None,
        )
        self.assertIsNone(buildroot_mount)

    def test_docker_config_auto_mounts_buildroot_when_present(self) -> None:
        """Buildroot is auto-mounted when nanvix_dir/buildroot exists."""
        script = self._make_script()
        buildroot_dir = script.nanvix_dir / "buildroot"
        buildroot_dir.mkdir(parents=True, exist_ok=True)
        cfg = script.docker_config("test-image")
        buildroot_mount = next(
            (m for m in cfg.mounts if m.container_path == BUILDROOT_CONTAINER_PATH),
            None,
        )
        self.assertIsNotNone(buildroot_mount)
        assert buildroot_mount is not None
        self.assertEqual(buildroot_mount.host_path, buildroot_dir)
        self.assertTrue(buildroot_mount.readonly)

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

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_run_with_docker_wraps_command(self, *_mocks: object) -> None:
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

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.run("make", "all")

        self.assertTrue(captured)
        self.assertIn("docker", captured[0])
        self.assertIn("make", captured[0])
        self.assertIn("all", captured[0])

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_run_env_forwarded_into_container(self, *_mocks: object) -> None:
        """env vars passed to run() are forwarded as -e flags in Docker mode."""
        script = self._make_script()
        script.docker = DockerConfig(
            image="nanvix/toolchain:latest-minimal",
            mounts=[],
            uid=1000,
            gid=1000,
        )
        captured_kwargs: list[dict[str, object]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_kwargs.append(dict(kwargs))
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.run("make", "all", env={"MY_VAR": "hello"})

        self.assertTrue(captured_kwargs)
        # env should NOT be passed to the docker subprocess itself
        self.assertIsNone(captured_kwargs[0].get("env"))

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_run_env_forwarded_into_container_as_flags(self, *_mocks: object) -> None:
        """env vars appear as -e KEY=VAL in the docker run command."""
        script = self._make_script()
        script.docker = DockerConfig(
            image="nanvix/toolchain:latest-minimal",
            mounts=[],
            uid=1000,
            gid=1000,
        )
        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.run("make", "all", env={"MY_VAR": "hello"})

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # -e MY_VAR=hello must appear in the docker run command
        self.assertIn("-e", cmd)
        self.assertIn("MY_VAR=hello", cmd)


class TestZScriptCleanWindows(unittest.TestCase):
    """ZScript.clean() Windows behavior."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_clean_removes_configured_artifact(self, _mock: object) -> None:
        """clean() removes .nanvix-configured on Windows."""
        script = ZScript(Path(self._tmpdir.name))
        artifact = script.repo_root / ".nanvix-configured"
        artifact.write_text("marker")
        script.clean()
        self.assertFalse(artifact.exists())

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_clean_noop_when_no_artifacts(self, _mock: object) -> None:
        """clean() does not raise when no artifacts exist on Windows."""
        script = ZScript(Path(self._tmpdir.name))
        script.clean()  # Should not raise.

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_clean_noop_on_linux(self, _mock: object) -> None:
        """clean() is a no-op on Linux (base class)."""
        script = ZScript(Path(self._tmpdir.name))
        script.clean()  # Should not raise.


class TestZScriptSetupLocalRefs(unittest.TestCase):
    """setup() handles LOCAL refs correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_local_sysroot_passes_local_path(self) -> None:
        """setup() passes local_path to Sysroot.download for LOCAL sysroot."""
        from nanvix_zutil.buildroot import Ref, RefKind

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch(
            "nanvix_zutil.script.Sysroot.download",
            return_value=fake_sysroot,
        ) as mock_download:
            script = ZScript(Path(self._tmpdir.name))
            script.manifest.sysroot_ref = Ref(
                kind=RefKind.LOCAL, value="/tmp/my-sysroot"
            )
            script.setup()

        _, kwargs = mock_download.call_args
        self.assertEqual(kwargs["local_path"], Path("/tmp/my-sysroot"))

    def test_non_local_sysroot_no_local_path(self) -> None:
        """setup() passes local_path=None for non-LOCAL sysroot."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch(
            "nanvix_zutil.script.Sysroot.download",
            return_value=fake_sysroot,
        ) as mock_download:
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        _, kwargs = mock_download.call_args
        self.assertIsNone(kwargs.get("local_path"))

    def test_local_sysroot_with_version_deps_exits(self) -> None:
        """setup() fatally errors when LOCAL sysroot + VERSION deps."""
        from nanvix_zutil.buildroot import Ref, RefKind

        write_manifest(Path(self._tmpdir.name), MANIFEST_WITH_DEPS)

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        log_mod.set_json_mode(True)
        try:
            with (
                patch(
                    "nanvix_zutil.script.Sysroot.download",
                    return_value=fake_sysroot,
                ),
                self.assertRaises(SystemExit) as ctx,
            ):
                script = ZScript(Path(self._tmpdir.name))
                script.manifest.sysroot_ref = Ref(
                    kind=RefKind.LOCAL, value="/tmp/my-sysroot"
                )
                script.setup()

            self.assertEqual(ctx.exception.code, 3)
        finally:
            log_mod.set_json_mode(False)

    def test_local_dep_skips_github_resolution(self) -> None:
        """setup() skips resolve_release for LOCAL deps."""
        from nanvix_zutil.buildroot import Dependency, Ref, RefKind

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_buildroot = MagicMock()

        with (
            patch(
                "nanvix_zutil.script.Sysroot.download",
                return_value=fake_sysroot,
            ),
            patch(
                "nanvix_zutil.script.Buildroot.create",
                return_value=fake_buildroot,
            ),
            patch("nanvix_zutil.script.resolve_release") as mock_resolve,
            patch("nanvix_zutil.script.resolve_release_with_fallback") as mock_fallback,
        ):
            script = ZScript(Path(self._tmpdir.name))
            script.manifest.dependencies = [
                Dependency(
                    name="zlib",
                    repo="nanvix/zlib",
                    ref=Ref(kind=RefKind.LOCAL, value="/tmp/zlib-build"),
                )
            ]
            script.setup()

        mock_resolve.assert_not_called()
        mock_fallback.assert_not_called()
        fake_buildroot.install_dep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
