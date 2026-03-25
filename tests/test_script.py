# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.script (ZScript)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.script import ZScript
from tests.testutils import MANIFEST_WITH_DEPS, write_manifest


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

    def test_sysroot_initially_none(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        self.assertIsNone(script.sysroot)

    def test_buildroot_initially_none(self) -> None:
        script = ZScript(Path(self._tmpdir.name))
        self.assertIsNone(script.buildroot)

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
        fake_sysroot.commitish = "abc1234"

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
        fake_sysroot.commitish = "abc1234"

        fake_buildroot = MagicMock()

        with (
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch(
                "nanvix_zutil.script.Buildroot.create", return_value=fake_buildroot
            ) as mock_create,
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

        # Verify sysroot_commitish is forwarded to every install_dep call.
        for call in fake_buildroot.install_dep.call_args_list:
            _, install_kwargs = call
            self.assertEqual(
                install_kwargs["sysroot_commitish"], fake_sysroot.commitish
            )

        # buildroot attribute is set on the instance.
        self.assertIs(script.buildroot, fake_buildroot)

    def test_setup_no_deps_skips_buildroot(self) -> None:
        """setup() with no manifest dependencies leaves buildroot as None."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.commitish = ""

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        self.assertIsNone(script.buildroot)

    def test_setup_saves_config(self) -> None:
        """setup() persists the sysroot path to env.json."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.commitish = ""

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript(Path(self._tmpdir.name))
            script.setup()

        config_file = Path(self._tmpdir.name) / ".nanvix" / "env.json"
        self.assertTrue(config_file.exists())


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

    def test_distclean_preserves_config(self) -> None:
        config_file = self._nanvix() / "env.json"
        config_file.write_text("{}")
        self._make_script().distclean()
        self.assertTrue(config_file.exists())

    def test_distclean_noop_when_nothing_exists(self) -> None:
        """distclean() does not raise when artifact dirs are absent."""
        self._make_script().distclean()


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


if __name__ == "__main__":
    unittest.main()
