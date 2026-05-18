# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.script (ZScript)."""

import json
import os
import subprocess as sp
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.buildroot import RefKind
from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
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


class TestZScriptSyncConfigs(unittest.TestCase):
    """setup() syncs canonical configs into .nanvix/."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _run_setup(self) -> ZScript:
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()
        return script

    def test_setup_creates_config_files(self) -> None:
        """setup() creates config files under .nanvix/."""
        self._run_setup()
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        self.assertTrue((nanvix_dir / "pyrightconfig.json").exists())
        self.assertTrue((nanvix_dir / ".yamllint.yml").exists())
        self.assertTrue((nanvix_dir / "black.toml").exists())
        self.assertTrue((nanvix_dir / ".gitignore").exists())

    def test_gitignore_contains_expected_patterns(self) -> None:
        """Synced .gitignore includes transient artifact patterns."""
        self._run_setup()
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        content = (nanvix_dir / ".gitignore").read_text()
        for pattern in ("venv/", "cache/", "sysroot/", "__pycache__/"):
            self.assertIn(pattern, content)

    def test_gitignore_does_not_ignore_lockfile(self) -> None:
        """Synced .gitignore must not ignore nanvix.lock (committed for reproducibility)."""
        self._run_setup()
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        content = (nanvix_dir / ".gitignore").read_text()
        self.assertNotIn("nanvix.lock", content)

    def test_setup_skips_identical_configs(self) -> None:
        """setup() is a no-op for configs when content already matches."""
        self._run_setup()
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        pyright_cfg = nanvix_dir / "pyrightconfig.json"
        mtime_before = pyright_cfg.stat().st_mtime
        import time

        time.sleep(0.01)
        self._run_setup()
        mtime_after = pyright_cfg.stat().st_mtime
        self.assertEqual(mtime_before, mtime_after)

    def test_setup_updates_config_when_different(self) -> None:
        """setup() overwrites config when content differs."""
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        pyright_cfg = nanvix_dir / "pyrightconfig.json"
        pyright_cfg.write_text("{}")
        self._run_setup()
        self.assertNotEqual(pyright_cfg.read_text(), "{}")

    def test_setup_confines_configs_to_nanvix_dir(self) -> None:
        """setup() never writes config files outside .nanvix/."""
        self._run_setup()
        repo_root = Path(self._tmpdir.name)
        self.assertFalse((repo_root / "pyrightconfig.json").exists())
        self.assertFalse((repo_root / ".yamllint.yml").exists())

    def test_pyright_config_includes_dot_directory(self) -> None:
        """Synced pyrightconfig.json includes '.' so .nanvix/*.py is analyzed."""
        import json

        self._run_setup()
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        cfg = json.loads((nanvix_dir / "pyrightconfig.json").read_text())
        self.assertIn(".", cfg["include"])

    def test_pyright_config_scoped_to_nanvix_dir(self) -> None:
        """Synced pyrightconfig.json does not include paths outside .nanvix/."""
        import json

        self._run_setup()
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        cfg = json.loads((nanvix_dir / "pyrightconfig.json").read_text())
        for entry in cfg["include"]:
            self.assertFalse(
                entry.startswith(".."),
                f"include entry '{entry}' escapes .nanvix/",
            )


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

    def test_run_timeout_exits(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                script.run(
                    sys.executable, "-c", "import time; time.sleep(10)", timeout=1
                )
            self.assertEqual(ctx.exception.code, 5)
        finally:
            log_mod.set_json_mode(False)

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_run_uses_windows_cmd_on_windows(self, _mock: object) -> None:
        """run() delegates to build_windows_run_cmd on Windows."""
        script = ZScript(Path(self._tmpdir.name))
        script.docker = DockerConfig(
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
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
        # Should use sh -c (Windows tar-copy mode).
        self.assertIn("sh", cmd)
        self.assertIn("-c", cmd)

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_run_dispatch_windows_default_docker(self, _mock: object) -> None:
        """run() uses build_windows_run_cmd on Windows even with default
        (empty) output_files — the dispatch no longer requires
        these fields to be populated."""
        script = ZScript(Path(self._tmpdir.name))
        script.docker = DockerConfig(
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
            mounts=[
                Mount(
                    host_path=script.repo_root,
                    container_path=WORKSPACE_CONTAINER_PATH,
                )
            ],
            uid=1000,
            gid=1000,
            # output_files intentionally left as defaults ([])
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
        # output_files.
        self.assertIn("sh", cmd)
        self.assertIn("-c", cmd)

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_run_dispatch_linux_uses_build_run_cmd(self, *_mocks: object) -> None:
        """run() uses build_run_cmd on Linux (not the Windows tar-copy path)."""
        script = ZScript(Path(self._tmpdir.name))
        script.docker = DockerConfig(
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
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
        import sys

        nanvixd = "bin/nanvixd.exe" if sys.platform == "win32" else "bin/nanvixd.elf"
        mkramfs = "bin/mkramfs.exe" if sys.platform == "win32" else "bin/mkramfs.elf"
        for mode in ("multi-process", "single-process", "standalone"):
            os.environ["NANVIX_DEPLOYMENT_MODE"] = mode
            script = ZScript(Path(self._tmpdir.name))
            files = script.sysroot_required_files()
            self.assertIn("lib/libposix.a", files, f"missing in {mode}")
            self.assertIn("lib/user.ld", files, f"missing in {mode}")
            self.assertIn(nanvixd, files, f"missing in {mode}")
            self.assertIn("bin/kernel.elf", files, f"missing in {mode}")
            self.assertIn(mkramfs, files, f"missing in {mode}")

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
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
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
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
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
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
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


class TestZScriptAutoDocker(unittest.TestCase):
    """Docker is always enabled for setup/build/release/clean (hard fail)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_build_auto_enables_docker_on_windows(self) -> None:
        """build on Windows uses the persisted Docker image."""

        class BuildScript(ZScript):
            def build(self) -> None:
                pass

        docker_configured = False

        def _fake_build(self_inner: ZScript) -> None:
            nonlocal docker_configured
            docker_configured = self_inner.docker is not None

        # Pre-persist Docker image so build can find it.
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        (nanvix_dir / "env.json").write_text(
            '{"NANVIX_DOCKER_IMAGE": "ghcr.io/nanvix/toolchain-gcc:sha-34a3641"}'
        )

        with (
            patch("sys.argv", ["z.py", "build"]),
            patch("nanvix_zutil.script.is_windows", return_value=True),
            patch("nanvix_zutil.script.docker_available", return_value=True),
            patch("nanvix_zutil.script.image_exists", return_value=True),
            patch.object(BuildScript, "build", _fake_build),
            patch("nanvix_zutil.script.log"),
        ):
            BuildScript.main(repo_root=Path(self._tmpdir.name))

        self.assertTrue(docker_configured)

    def test_build_auto_enables_docker_on_linux(self) -> None:
        """build on Linux uses the persisted Docker image."""

        class BuildScript(ZScript):
            def build(self) -> None:
                pass

        docker_configured = False

        def _fake_build(self_inner: ZScript) -> None:
            nonlocal docker_configured
            docker_configured = self_inner.docker is not None

        # Pre-persist Docker image so build can find it.
        nanvix_dir = Path(self._tmpdir.name) / ".nanvix"
        (nanvix_dir / "env.json").write_text(
            '{"NANVIX_DOCKER_IMAGE": "ghcr.io/nanvix/toolchain-gcc:sha-34a3641"}'
        )

        with (
            patch("sys.argv", ["z.py", "build"]),
            patch("nanvix_zutil.script.is_windows", return_value=False),
            patch("nanvix_zutil.script.docker_available", return_value=True),
            patch("nanvix_zutil.script.image_exists", return_value=True),
            patch.object(BuildScript, "build", _fake_build),
            patch("nanvix_zutil.script.log"),
        ):
            BuildScript.main(repo_root=Path(self._tmpdir.name))

        self.assertTrue(docker_configured)


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


class TestZScriptSetupFallbackReporting(unittest.TestCase):
    """setup() reports fallback state correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name), MANIFEST_WITH_DEPS)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_setup_returns_false_when_no_fallback(self) -> None:
        """setup() returns False when all deps resolve exactly."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        fake_buildroot = MagicMock()
        fake_release: dict[str, object] = {"tag_name": "1.0-nanvix-0.1.0"}

        with (
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch("nanvix_zutil.script.Buildroot.create", return_value=fake_buildroot),
            patch(
                "nanvix_zutil.script.resolve_release_with_fallback",
                return_value=(fake_release, None),  # None = no fallback
            ),
        ):
            script = ZScript(Path(self._tmpdir.name))
            result = script.setup()

        self.assertFalse(result)

    def test_setup_returns_true_when_fallback_used(self) -> None:
        """setup() returns True when a dep falls back to a different version."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        fake_buildroot = MagicMock()
        fake_release: dict[str, object] = {"tag_name": "1.0-nanvix-0.0.9"}

        with (
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch("nanvix_zutil.script.Buildroot.create", return_value=fake_buildroot),
            patch(
                "nanvix_zutil.script.resolve_release_with_fallback",
                return_value=(fake_release, "0.0.9"),  # non-None = fallback used
            ),
        ):
            script = ZScript(Path(self._tmpdir.name))
            result = script.setup()

        self.assertTrue(result)

    def test_setup_no_deps_returns_false(self) -> None:
        """setup() returns False when there are no dependencies."""
        write_manifest(Path(self._tmpdir.name))  # default manifest, no deps

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript(Path(self._tmpdir.name))
            result = script.setup()

        self.assertFalse(result)

    def test_setup_fallback_logs_warning(self) -> None:
        """setup() logs at warning level when fallback is used."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        fake_buildroot = MagicMock()
        fake_release: dict[str, object] = {"tag_name": "1.0-nanvix-0.0.9"}

        with (
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch("nanvix_zutil.script.Buildroot.create", return_value=fake_buildroot),
            patch(
                "nanvix_zutil.script.resolve_release_with_fallback",
                return_value=(fake_release, "0.0.9"),
            ),
            patch("nanvix_zutil.script.log") as mock_log,
        ):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        # Verify warning was called (not info) for fallback message.
        warning_calls = [
            call
            for call in mock_log.warning.call_args_list
            if "fallback" in str(call).lower()
        ]
        self.assertTrue(warning_calls, "Expected a warning log for fallback")

        # Verify info was NOT called for fallback (regression guard).
        info_calls = [
            call
            for call in mock_log.info.call_args_list
            if "fallback" in str(call).lower()
        ]
        self.assertFalse(info_calls, "Fallback should use warning, not info")


class TestZScriptMainDegradedExit(unittest.TestCase):
    """main() exits with EXIT_DEGRADED_SETUP when setup uses fallback."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name), MANIFEST_WITH_DEPS)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_exit_degraded_setup_value(self) -> None:
        """EXIT_DEGRADED_SETUP has the expected value of 7."""
        from nanvix_zutil.exitcodes import EXIT_DEGRADED_SETUP

        self.assertEqual(EXIT_DEGRADED_SETUP, 7)

    def test_main_exits_7_on_fallback_setup(self) -> None:
        """main() with setup subcommand exits 7 when fallback was used."""
        from nanvix_zutil.exitcodes import EXIT_DEGRADED_SETUP

        def _setup_with_fallback(self_inner: ZScript) -> bool:
            self_inner._used_fallback = True  # pyright: ignore[reportPrivateUsage]
            return True

        with (
            patch("sys.argv", ["z.py", "setup", "--with-docker", "test/image:tag"]),
            patch("nanvix_zutil.script.docker_available", return_value=True),
            patch("nanvix_zutil.script.image_exists", return_value=True),
            patch.object(ZScript, "setup", _setup_with_fallback),
            self.assertRaises(SystemExit) as ctx,
        ):
            ZScript.main(repo_root=Path(self._tmpdir.name))

        self.assertEqual(ctx.exception.code, EXIT_DEGRADED_SETUP)

    def test_main_exits_7_on_return_only_fallback_setup(self) -> None:
        """main() honors setup() returning True without touching private state."""
        from nanvix_zutil.exitcodes import EXIT_DEGRADED_SETUP

        with (
            patch("sys.argv", ["z.py", "setup", "--with-docker", "test/image:tag"]),
            patch("nanvix_zutil.script.docker_available", return_value=True),
            patch("nanvix_zutil.script.image_exists", return_value=True),
            patch.object(ZScript, "setup", return_value=True),
            self.assertRaises(SystemExit) as ctx,
        ):
            ZScript.main(repo_root=Path(self._tmpdir.name))

        self.assertEqual(ctx.exception.code, EXIT_DEGRADED_SETUP)

    def test_main_exits_0_on_clean_setup(self) -> None:
        """main() with setup subcommand completes normally when no fallback."""
        with (
            patch("sys.argv", ["z.py", "setup", "--with-docker", "test/image:tag"]),
            patch("nanvix_zutil.script.docker_available", return_value=True),
            patch("nanvix_zutil.script.image_exists", return_value=True),
            patch.object(ZScript, "setup", return_value=False),
            patch("nanvix_zutil.script.log") as mock_log,
        ):
            # Should not raise SystemExit.
            ZScript.main(repo_root=Path(self._tmpdir.name))

        # Verify success log was emitted.
        success_calls = [
            call
            for call in mock_log.success.call_args_list
            if "complete" in str(call).lower()
        ]
        self.assertTrue(success_calls, "Expected a success log on clean setup")

    def test_main_json_warning_includes_degraded_code(self) -> None:
        """main() emits a warning JSON object with code on degraded setup."""
        from nanvix_zutil.exitcodes import EXIT_DEGRADED_SETUP

        buf = StringIO()
        original_stderr = sys.stderr
        log_mod.set_json_mode(True)
        sys.stderr = buf
        try:
            with (
                patch(
                    "sys.argv",
                    ["z.py", "--json", "setup", "--with-docker", "test/image:tag"],
                ),
                patch("nanvix_zutil.script.docker_available", return_value=True),
                patch("nanvix_zutil.script.image_exists", return_value=True),
                patch.object(ZScript, "setup", return_value=True),
                self.assertRaises(SystemExit) as ctx,
            ):
                ZScript.main(repo_root=Path(self._tmpdir.name))
        finally:
            sys.stderr = original_stderr
            log_mod.set_json_mode(False)

        self.assertEqual(ctx.exception.code, EXIT_DEGRADED_SETUP)
        json_lines = [ln for ln in buf.getvalue().splitlines() if ln.startswith("{")]
        self.assertTrue(json_lines, "Expected JSON output on stderr")
        obj = json.loads(json_lines[-1])
        self.assertEqual(obj["level"], "warning")
        self.assertEqual(obj["code"], EXIT_DEGRADED_SETUP)


class TestZScriptSetupWithNanvix(unittest.TestCase):
    """setup() with WITH_NANVIX overlays local artifacts."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))
        for key in (
            "NANVIX_MACHINE",
            "NANVIX_DEPLOYMENT_MODE",
            "NANVIX_MEMORY_SIZE",
            "WITH_NANVIX",
        ):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        os.environ.pop("WITH_NANVIX", None)
        self._tmpdir.cleanup()

    def test_setup_calls_overlay_when_env_set(self) -> None:
        """setup() calls sysroot.overlay_local_nanvix when WITH_NANVIX is set."""
        local_dir = Path(self._tmpdir.name) / "local-nanvix"
        local_dir.mkdir()

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        os.environ["WITH_NANVIX"] = str(local_dir)

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        fake_sysroot.overlay_local_nanvix.assert_called_once_with(local_dir)
        fake_sysroot.verify.assert_called_once()

    def test_setup_no_overlay_without_env(self) -> None:
        """setup() does not call overlay_local_nanvix when WITH_NANVIX is unset."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        fake_sysroot.overlay_local_nanvix.assert_not_called()

    def test_setup_local_deps_skips_github(self) -> None:
        """setup() skips GitHub download for deps found locally."""
        write_manifest(Path(self._tmpdir.name), MANIFEST_WITH_DEPS)

        local_dir = Path(self._tmpdir.name) / "local-nanvix"
        (local_dir / "deps" / "zlib" / "lib").mkdir(parents=True)
        (local_dir / "deps" / "zlib" / "lib" / "libz.a").write_bytes(b"local-zlib")

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.tag = "v0.1.0"

        os.environ["WITH_NANVIX"] = str(local_dir)

        with (
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch("nanvix_zutil.script.resolve_release_with_fallback") as mock_resolve,
        ):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        # The dependency was satisfied locally so GitHub resolve should not
        # have been called.
        mock_resolve.assert_not_called()


class TestZScriptLint(unittest.TestCase):
    """Tests for ZScript.lint() default implementation."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_script(self) -> ZScript:
        return ZScript(Path(self._tmpdir.name))

    def test_lint_runs_black_and_pyright(self) -> None:
        """lint() runs black --check and pyright on .nanvix/*.py files."""
        script = self._make_script()
        # Create a .py file in .nanvix/
        py_file = script.nanvix_dir / "z.py"
        py_file.write_text("x = 1\n")

        calls: list[list[str]] = []

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            calls.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        with (
            patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run),
            patch("importlib.util.find_spec", return_value=True),
        ):
            script.lint()

        self.assertEqual(len(calls), 2)
        self.assertIn("-m", calls[0])
        self.assertIn("black", calls[0])
        self.assertIn("--config", calls[0])
        self.assertIn("--check", calls[0])
        self.assertIn("-m", calls[1])
        self.assertIn("pyright", calls[1])
        self.assertIn("--project", calls[1])

    def test_lint_no_py_files_warns(self) -> None:
        """lint() warns and returns when no .py files exist."""
        script = self._make_script()
        # Remove all .py files from .nanvix/
        for f in script.nanvix_dir.glob("*.py"):
            f.unlink()

        with patch("nanvix_zutil.script.log") as mock_log:
            script.lint()

        mock_log.warning.assert_called_once()
        self.assertIn("nothing to lint", mock_log.warning.call_args[0][0].lower())

    def test_lint_exits_on_black_failure(self) -> None:
        """lint() exits with EXIT_BUILD_FAILURE when black fails."""
        script = self._make_script()
        py_file = script.nanvix_dir / "z.py"
        py_file.write_text("x = 1\n")

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            return sp.CompletedProcess(args=cmd, returncode=1)

        log_mod.set_json_mode(True)
        try:
            with (
                patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run),
                patch("importlib.util.find_spec", return_value=True),
                self.assertRaises(SystemExit) as ctx,
            ):
                script.lint()
            self.assertEqual(ctx.exception.code, 5)
        finally:
            log_mod.set_json_mode(False)

    def test_lint_exits_when_tool_missing(self) -> None:
        """lint() exits with EXIT_MISSING_DEP when black is not installed."""
        script = self._make_script()
        py_file = script.nanvix_dir / "z.py"
        py_file.write_text("x = 1\n")

        log_mod.set_json_mode(True)
        try:
            with (
                patch("importlib.util.find_spec", return_value=None),
                self.assertRaises(SystemExit) as ctx,
            ):
                script.lint()
            self.assertEqual(ctx.exception.code, 3)
        finally:
            log_mod.set_json_mode(False)


class TestZScriptFormat(unittest.TestCase):
    """Tests for ZScript.format() default implementation."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_script(self) -> ZScript:
        return ZScript(Path(self._tmpdir.name))

    def test_format_runs_black(self) -> None:
        """format() runs black on .nanvix/*.py files."""
        script = self._make_script()
        py_file = script.nanvix_dir / "z.py"
        py_file.write_text("x = 1\n")

        calls: list[list[str]] = []

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            calls.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        with (
            patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run),
            patch("importlib.util.find_spec", return_value=True),
        ):
            script.format()

        self.assertEqual(len(calls), 1)
        self.assertIn("-m", calls[0])
        self.assertIn("black", calls[0])
        self.assertIn("--config", calls[0])
        self.assertNotIn("--check", calls[0])

    def test_format_check_mode(self) -> None:
        """format(check=True) runs black --check."""
        script = self._make_script()
        py_file = script.nanvix_dir / "z.py"
        py_file.write_text("x = 1\n")

        calls: list[list[str]] = []

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            calls.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        with (
            patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run),
            patch("importlib.util.find_spec", return_value=True),
        ):
            script.format(check=True)

        self.assertEqual(len(calls), 1)
        self.assertIn("-m", calls[0])
        self.assertIn("black", calls[0])
        self.assertIn("--config", calls[0])
        self.assertIn("--check", calls[0])

    def test_format_no_py_files_warns(self) -> None:
        """format() warns and returns when no .py files exist."""
        script = self._make_script()
        for f in script.nanvix_dir.glob("*.py"):
            f.unlink()

        with patch("nanvix_zutil.script.log") as mock_log:
            script.format()

        mock_log.warning.assert_called_once()
        self.assertIn("nothing to format", mock_log.warning.call_args[0][0].lower())

    def test_format_exits_on_failure(self) -> None:
        """format() exits with EXIT_BUILD_FAILURE when black fails."""
        script = self._make_script()
        py_file = script.nanvix_dir / "z.py"
        py_file.write_text("x = 1\n")

        def fake_run(
            args: tuple[str, ...], **kwargs: object
        ) -> sp.CompletedProcess[str]:
            cmd = list(args)
            return sp.CompletedProcess(args=cmd, returncode=1)

        log_mod.set_json_mode(True)
        try:
            with (
                patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run),
                patch("importlib.util.find_spec", return_value=True),
                self.assertRaises(SystemExit) as ctx,
            ):
                script.format()
            self.assertEqual(ctx.exception.code, 5)
        finally:
            log_mod.set_json_mode(False)


class TestZScriptLintInAutoHooks(unittest.TestCase):
    """lint and format appear in AUTO_HOOKS and available_subcommands."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_lint_in_auto_hooks(self) -> None:
        self.assertIn("lint", ZScript.AUTO_HOOKS)

    def test_format_in_auto_hooks(self) -> None:
        self.assertIn("format", ZScript.AUTO_HOOKS)

    def test_lint_always_available(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        self.assertIn("lint", script.available_subcommands())

    def test_format_always_available(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        self.assertIn("format", script.available_subcommands())


class TestZScriptSetupLocalSysroot(unittest.TestCase):
    """setup() with RefKind.LOCAL sysroot uses from_local, no GitHub."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_local_sysroot_skips_github(self) -> None:
        """When sysroot ref is LOCAL, Sysroot.from_local is used."""
        repo_root = Path(self._tmpdir.name)
        # Create a local sysroot directory.
        local_sysroot = repo_root / "my-sysroot"
        local_sysroot.mkdir()

        write_manifest(repo_root)

        with patch.dict(os.environ, {"NANVIX_VERSION": str(local_sysroot)}):
            script = ZScript(repo_root)

        # Verify manifest parsed as LOCAL.
        self.assertEqual(script.manifest.sysroot_ref.kind, RefKind.LOCAL)

        with (
            patch("nanvix_zutil.script.Sysroot.download") as mock_download,
            patch(
                "nanvix_zutil.script.Sysroot.from_local",
                return_value=MagicMock(path=local_sysroot, tag=""),
            ) as mock_from_local,
        ):
            script.setup()
            mock_download.assert_not_called()
            mock_from_local.assert_called_once()

    def test_local_sysroot_no_dep_suffix(self) -> None:
        """When sysroot is LOCAL, VERSION deps are not suffixed."""
        repo_root = Path(self._tmpdir.name)
        local_sysroot = repo_root / "my-sysroot"
        local_sysroot.mkdir()

        write_manifest(repo_root, MANIFEST_WITH_DEPS)

        with patch.dict(os.environ, {"NANVIX_VERSION": str(local_sysroot)}):
            script = ZScript(repo_root)

        # VERSION dep should NOT be suffixed (sysroot is LOCAL).
        self.assertEqual(script.manifest.dependencies[0].ref.value, "1.0")


class TestZScriptSetupLocalDep(unittest.TestCase):
    """setup() with RefKind.LOCAL dep installs from filesystem."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_local_dep_uses_install_local_nanvix(self) -> None:
        """LOCAL deps call install_local_nanvix instead of GitHub."""
        repo_root = Path(self._tmpdir.name)
        local_dep_path = repo_root / "local-zlib"
        # Create the expected local layout.
        (local_dep_path / "deps" / "zlib" / "lib").mkdir(parents=True)
        (local_dep_path / "deps" / "zlib" / "lib" / "libz.a").write_bytes(b"fake")

        write_manifest(repo_root, MANIFEST_WITH_DEPS)

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.tag = "v0.1.0"

        with (
            patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": str(local_dep_path)}),
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
        ):
            script = ZScript(repo_root)
            script.setup()

        # Dep was installed locally, buildroot should exist.
        self.assertIsNotNone(script.buildroot)
        # The local lib should have been copied into the buildroot.
        buildroot_lib = script.buildroot.path / "lib" / "libz.a"  # type: ignore[union-attr]
        self.assertTrue(buildroot_lib.exists())

    def test_local_dep_no_github_call(self) -> None:
        """LOCAL deps do not call resolve_release or download_release_asset."""
        repo_root = Path(self._tmpdir.name)
        local_dep_path = repo_root / "local-zlib"
        (local_dep_path / "deps" / "zlib" / "lib").mkdir(parents=True)
        (local_dep_path / "deps" / "zlib" / "lib" / "libz.a").write_bytes(b"fake")

        write_manifest(repo_root, MANIFEST_WITH_DEPS)

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.tag = "v0.1.0"

        with (
            patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": str(local_dep_path)}),
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch("nanvix_zutil.script.resolve_release") as mock_resolve,
            patch("nanvix_zutil.script.resolve_release_with_fallback") as mock_fallback,
        ):
            script = ZScript(repo_root)
            script.setup()

        mock_resolve.assert_not_called()
        mock_fallback.assert_not_called()

    def test_local_dep_missing_artifacts_exits(self) -> None:
        """LOCAL dep with no artifacts at the path exits with code 3."""
        repo_root = Path(self._tmpdir.name)
        local_dep_path = repo_root / "empty-dir"
        local_dep_path.mkdir()

        write_manifest(repo_root, MANIFEST_WITH_DEPS)

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.tag = "v0.1.0"

        log_mod.set_json_mode(True)
        with (
            patch.dict(os.environ, {"NANVIX_VERSION_ZLIB": str(local_dep_path)}),
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            self.assertRaises(SystemExit) as ctx,
        ):
            script = ZScript(repo_root)
            script.setup()

        self.assertEqual(ctx.exception.code, 3)


class TestMakeInitrd(unittest.TestCase):
    """ZScript.make_initrd() builds the correct mkimage command."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        write_manifest(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_script(self) -> ZScript:
        repo_root = Path(self._tmpdir.name)
        script = ZScript(repo_root)
        # Set up a fake sysroot with a bin/ directory and mkimage stub.
        sysroot_bin = repo_root / ".nanvix" / "sysroot" / "bin"
        sysroot_bin.mkdir(parents=True, exist_ok=True)
        (sysroot_bin / "mkimage.elf").touch()
        (sysroot_bin / "mkimage.exe").touch()
        fake_sysroot = MagicMock()
        fake_sysroot.path = repo_root / ".nanvix" / "sysroot"
        script.sysroot = fake_sysroot
        return script

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_basic_invocation_linux(self, _mock: object) -> None:
        """Produces the expected command on Linux."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            result = script.make_initrd("my-app.elf")

        self.assertEqual(result, script.repo_root / "my-app.img")
        cmd = captured[0]
        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(cmd[0], str(bin_dir / "mkimage.elf"))
        self.assertEqual(cmd[1], "-o")
        self.assertEqual(cmd[2], str(script.repo_root / "my-app.img"))
        self.assertEqual(cmd[3], f"{bin_dir / 'procd.elf'};procd")
        self.assertEqual(cmd[4], f"{bin_dir / 'memd.elf'};memd")
        self.assertEqual(cmd[5], f"{bin_dir / 'vfsd.elf'};vfsd")
        self.assertEqual(cmd[6], f"{script.repo_root / 'my-app.elf'};my-app")

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_basic_invocation_windows(self, _mock: object) -> None:
        """Uses mkimage.exe on Windows."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd("my-app.elf")

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(captured[0][0], str(bin_dir / "mkimage.exe"))

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_app_args(self, _mock: object) -> None:
        """App arguments are appended to the app entry."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd("my-app.elf", app_args=["--verbose", "--port=8080"])

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry, f"{script.repo_root / 'my-app.elf'};my-app --verbose --port=8080"
        )

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_daemon_args(self, _mock: object) -> None:
        """Daemon arguments are appended to respective entries."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd(
                "my-app.elf",
                procd_args=["--debug"],
                memd_args=["--heap=64m"],
                vfsd_args=["--cache=off"],
            )

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(captured[0][3], f"{bin_dir / 'procd.elf'};procd --debug")
        self.assertEqual(captured[0][4], f"{bin_dir / 'memd.elf'};memd --heap=64m")
        self.assertEqual(captured[0][5], f"{bin_dir / 'vfsd.elf'};vfsd --cache=off")

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_kernel_args(self, _mock: object) -> None:
        """Kernel arguments are passed via --kernel-args."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd("my-app.elf", kernel_args=["console=ttyS0", "debug"])

        cmd = captured[0]
        # -kernel-args should appear after -o <output>
        ka_idx = cmd.index("-kernel-args")
        self.assertEqual(cmd[ka_idx + 1], "console=ttyS0 debug")

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_semicolons_escaped_in_args(self, _mock: object) -> None:
        """Semicolons in arguments are escaped as \\;."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd("my-app.elf", app_args=["--sep=;"])

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry, f"{script.repo_root / 'my-app.elf'};my-app --sep=\\;"
        )

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_semicolons_escaped_in_kernel_args(self, _mock: object) -> None:
        """Semicolons in kernel arguments are escaped."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd("my-app.elf", kernel_args=["a;b"])

        cmd = captured[0]
        ka_idx = cmd.index("-kernel-args")
        self.assertEqual(cmd[ka_idx + 1], "a\\;b")

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_custom_bin_dir(self, _mock: object) -> None:
        """A custom bin_dir is used instead of the sysroot."""
        script = self._make_script()
        custom_bin = Path(self._tmpdir.name) / "custom" / "bin"
        custom_bin.mkdir(parents=True)
        (custom_bin / "mkimage.elf").touch()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd("my-app.elf", bin_dir=custom_bin)

        self.assertEqual(captured[0][0], str(custom_bin / "mkimage.elf"))
        self.assertIn(str(custom_bin / "procd.elf"), captured[0][3])

    def test_no_sysroot_exits(self) -> None:
        """Exits with EXIT_MISSING_DEP when sysroot is None."""
        script = ZScript(Path(self._tmpdir.name))
        script.sysroot = None
        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                script.make_initrd("my-app.elf")
            self.assertEqual(ctx.exception.code, 3)
        finally:
            log_mod.set_json_mode(False)

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_app_stem_derived_from_filename(self, _mock: object) -> None:
        """The output .img and argv0 use the stem of the app filename."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            result = script.make_initrd("hello-world.elf")

        self.assertEqual(result, script.repo_root / "hello-world.img")
        self.assertEqual(
            captured[0][6], f"{script.repo_root / 'hello-world.elf'};hello-world"
        )

    def test_app_with_path_separator_exits(self) -> None:
        """Exits with EXIT_MISSING_DEP when app contains path separators."""
        script = self._make_script()
        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                script.make_initrd("build/hello.elf")
            self.assertEqual(ctx.exception.code, 3)
        finally:
            log_mod.set_json_mode(False)

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_mkimage_not_found_exits(self, _mock: object) -> None:
        """Exits with EXIT_MISSING_DEP when mkimage binary is missing."""
        repo_root = Path(self._tmpdir.name)
        script = ZScript(repo_root)
        # Sysroot bin dir exists but mkimage.elf does not.
        sysroot_bin = repo_root / ".nanvix" / "sysroot" / "bin"
        sysroot_bin.mkdir(parents=True, exist_ok=True)
        fake_sysroot = MagicMock()
        fake_sysroot.path = repo_root / ".nanvix" / "sysroot"
        script.sysroot = fake_sysroot
        log_mod.set_json_mode(True)
        try:
            with self.assertRaises(SystemExit) as ctx:
                script.make_initrd("my-app.elf")
            self.assertEqual(ctx.exception.code, 3)
        finally:
            log_mod.set_json_mode(False)

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_app_env(self, _mock: object) -> None:
        """Environment variables are appended after a semicolon separator."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd("my-app.elf", app_env=["VAR1=foo", "VAR2=bar"])

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry,
            f"{script.repo_root / 'my-app.elf'};my-app;VAR1=foo VAR2=bar",
        )

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_app_args_and_env(self, _mock: object) -> None:
        """Both app arguments and environment variables are emitted."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd(
                "my-app.elf",
                app_args=["--verbose"],
                app_env=["DEBUG=1"],
            )

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry,
            f"{script.repo_root / 'my-app.elf'};my-app --verbose;DEBUG=1",
        )

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_daemon_env(self, _mock: object) -> None:
        """Daemon environment variables are appended to respective entries."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd(
                "my-app.elf",
                procd_env=["LOG=debug"],
                memd_env=["HEAP=64m"],
                vfsd_env=["CACHE=off"],
            )

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(captured[0][3], f"{bin_dir / 'procd.elf'};procd;LOG=debug")
        self.assertEqual(captured[0][4], f"{bin_dir / 'memd.elf'};memd;HEAP=64m")
        self.assertEqual(captured[0][5], f"{bin_dir / 'vfsd.elf'};vfsd;CACHE=off")

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_env_semicolons_escaped(self, _mock: object) -> None:
        """Semicolons in env values are escaped."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd("my-app.elf", app_env=["PATH=/a;/b"])

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry,
            f"{script.repo_root / 'my-app.elf'};my-app;PATH=/a\\;/b",
        )

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_daemon_args_and_env(self, _mock: object) -> None:
        """Daemon entries include both CLI arguments and environment variables."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd(
                "my-app.elf",
                procd_args=["--log-level", "trace"],
                procd_env=["LOG=debug"],
            )

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(
            captured[0][3],
            f"{bin_dir / 'procd.elf'};procd --log-level trace;LOG=debug",
        )

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_env_windows(self, _mock: object) -> None:
        """Environment variables work correctly on Windows (mkimage.exe)."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.script.subprocess.run", side_effect=fake_run):
            script.make_initrd(
                "my-app.elf",
                app_args=["--verbose"],
                app_env=["DEBUG=1"],
                procd_env=["LOG=debug"],
            )

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(captured[0][0], str(bin_dir / "mkimage.exe"))
        self.assertEqual(captured[0][3], f"{bin_dir / 'procd.elf'};procd;LOG=debug")
        self.assertEqual(
            captured[0][6],
            f"{script.repo_root / 'my-app.elf'};my-app --verbose;DEBUG=1",
        )


if __name__ == "__main__":
    unittest.main()
