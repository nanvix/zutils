# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# pyright: reportPrivateUsage=false
"""Tests for nanvix_zutil.script (ZScript)."""

import os
import subprocess as sp
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanvix_zutil import helpers, paths
from nanvix_zutil.buildroot import RefKind
from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
)
from nanvix_zutil.exitcodes import EXIT_BUILD_FAILURE, EXIT_MISSING_DEP
from nanvix_zutil.helpers import InitRdArgs
from nanvix_zutil.script import ZScript
from tests.testutils import (
    MANIFEST_LATEST_WITH_DEPS,
    MANIFEST_WITH_DEPS,
    write_manifest,
)


class TestZScriptInit(unittest.TestCase):
    """ZScript initialises correctly."""

    def setUp(self) -> None:
        write_manifest()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_repo_root_resolved(self) -> None:
        repo_root = paths.repo_root()
        self.assertEqual(paths.repo_root(), repo_root.resolve())

    def test_nanvix_dir(self) -> None:
        repo_root = paths.repo_root()
        self.assertEqual(paths.nanvix_root(), repo_root.resolve() / ".nanvix")

    def test_config_accessible(self) -> None:
        script = ZScript()
        self.assertEqual(script.config.machine, "microvm")

    def test_manifest_loaded(self) -> None:
        script = ZScript()
        self.assertEqual(script.manifest.sysroot_ref.value, "0.1.0")

    def test_sysroot_initially_none(self) -> None:
        script = ZScript()
        self.assertIsNone(script.sysroot)

    def test_buildroot_initially_none(self) -> None:
        script = ZScript()
        self.assertIsNone(script.buildroot)

    def test_log_attribute_is_log_module(self) -> None:
        """self.log refers to the nanvix_zutil.log module."""
        import nanvix_zutil.log as log_module

        script = ZScript()
        self.assertIs(script.log, log_module)

    def test_missing_manifest_exits_3(self) -> None:
        # Autouse fixture wrote a manifest in setUp; remove it.
        (paths.manifest_path()).unlink()
        with self.assertRaises(SystemExit) as ctx:
            ZScript()
        self.assertEqual(ctx.exception.code, 3)


class TestZScriptAutoSetup(unittest.TestCase):
    """Base setup() auto-downloads sysroot and dependencies."""

    def setUp(self) -> None:
        write_manifest()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_setup_downloads_sysroot(self) -> None:
        """setup() calls Sysroot.download with config values."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch(
            "nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot
        ) as mock_download:
            script = ZScript()
            script.setup()

            # Assert Sysroot.download was called once with the expected kwargs.
            mock_download.assert_called_once()
            _, kwargs = mock_download.call_args
            self.assertEqual(kwargs["machine"], script.config.machine)
            self.assertEqual(kwargs["deployment_mode"], script.config.deployment_mode)
            self.assertEqual(kwargs["memory_size"], script.config.memory_size)
            self.assertEqual(kwargs["tag"], script.manifest.sysroot_ref.value)
            self.assertIsInstance(kwargs["dest"], Path)
            self.assertTrue(str(kwargs["dest"]).startswith(str(paths.nanvix_root())))
            self.assertIs(kwargs["config"], script.config)
        fake_sysroot.verify.assert_called_once()
        self.assertIs(script.sysroot, fake_sysroot)

    def test_setup_with_deps_creates_buildroot(self) -> None:
        """setup() with manifest dependencies creates Buildroot and installs all deps."""
        write_manifest(MANIFEST_WITH_DEPS)

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
            script = ZScript()
            script.setup()

        # Buildroot.create called once (now takes no arguments).
        mock_create.assert_called_once_with()

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
            script = ZScript()
            script.setup()

        self.assertIsNone(script.buildroot)

    def test_setup_saves_config(self) -> None:
        """setup() persists the sysroot path to env.json."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript()
            script.setup()

        config_file = paths.nanvix_root() / "env.json"
        self.assertTrue(config_file.exists())


class TestZScriptSetupLatestSysroot(unittest.TestCase):
    """setup() with nanvix-version = "latest" suffixes deps correctly."""

    def setUp(self) -> None:
        write_manifest(MANIFEST_LATEST_WITH_DEPS)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

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
            script = ZScript()
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

        with (
            patch(
                "nanvix_zutil.script.Sysroot.download",
                return_value=fake_sysroot,
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            script = ZScript()
            script.setup()

        self.assertEqual(ctx.exception.code, 3)


class TestZScriptSyncConfigs(unittest.TestCase):
    """setup() syncs canonical configs into .nanvix/."""

    def setUp(self) -> None:
        write_manifest()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def _run_setup(self) -> ZScript:
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript()
            script.setup()
        return script

    def test_setup_creates_config_files(self) -> None:
        """setup() creates config files under .nanvix/."""
        self._run_setup()
        nanvix_dir = paths.nanvix_root()
        self.assertTrue((nanvix_dir / "pyrightconfig.json").exists())
        self.assertTrue((nanvix_dir / ".yamllint.yml").exists())
        self.assertTrue((nanvix_dir / "black.toml").exists())
        self.assertTrue((nanvix_dir / ".gitignore").exists())

    def test_gitignore_contains_expected_patterns(self) -> None:
        """Synced .gitignore includes transient artifact patterns."""
        self._run_setup()
        nanvix_dir = paths.nanvix_root()
        content = (nanvix_dir / ".gitignore").read_text()
        # Patterns are intentionally written without trailing slashes so they
        # also match symlinks pointing at directories (e.g. sysroot -> ...).
        for pattern in ("venv", "cache", "sysroot", "__pycache__"):
            self.assertIn(pattern, content)

    def test_gitignore_does_not_ignore_lockfile(self) -> None:
        """Synced .gitignore must not ignore nanvix.lock (committed for reproducibility)."""
        self._run_setup()
        nanvix_dir = paths.nanvix_root()
        content = (nanvix_dir / ".gitignore").read_text()
        self.assertNotIn("nanvix.lock", content)

    def test_setup_skips_identical_configs(self) -> None:
        """setup() is a no-op for configs when content already matches."""
        self._run_setup()
        nanvix_dir = paths.nanvix_root()
        pyright_cfg = nanvix_dir / "pyrightconfig.json"
        mtime_before = pyright_cfg.stat().st_mtime
        import time

        time.sleep(0.01)
        self._run_setup()
        mtime_after = pyright_cfg.stat().st_mtime
        self.assertEqual(mtime_before, mtime_after)

    def test_setup_updates_config_when_different(self) -> None:
        """setup() overwrites config when content differs."""
        nanvix_dir = paths.nanvix_root()
        pyright_cfg = nanvix_dir / "pyrightconfig.json"
        pyright_cfg.write_text("{}")
        self._run_setup()
        self.assertNotEqual(pyright_cfg.read_text(), "{}")

    def test_setup_confines_configs_to_nanvix_dir(self) -> None:
        """setup() never writes config files outside .nanvix/."""
        self._run_setup()
        repo_root = paths.repo_root()
        self.assertFalse((repo_root / "pyrightconfig.json").exists())
        self.assertFalse((repo_root / ".yamllint.yml").exists())

    def test_pyright_config_includes_dot_directory(self) -> None:
        """Synced pyrightconfig.json includes '.' so .nanvix/*.py is analyzed."""
        import json

        self._run_setup()
        nanvix_dir = paths.nanvix_root()
        cfg = json.loads((nanvix_dir / "pyrightconfig.json").read_text())
        self.assertIn(".", cfg["include"])

    def test_pyright_config_scoped_to_nanvix_dir(self) -> None:
        """Synced pyrightconfig.json does not include paths outside .nanvix/."""
        import json

        self._run_setup()
        nanvix_dir = paths.nanvix_root()
        cfg = json.loads((nanvix_dir / "pyrightconfig.json").read_text())
        for entry in cfg["include"]:
            self.assertFalse(
                entry.startswith(".."),
                f"include entry '{entry}' escapes .nanvix/",
            )


class TestZScriptLifecycleHooks(unittest.TestCase):
    """Default consumer lifecycle hooks are no-ops."""

    def setUp(self) -> None:
        write_manifest()

    def _make_script(self) -> ZScript:
        return ZScript()

    def test_build_noop(self) -> None:
        self._make_script().build()

    def test_test_noop(self) -> None:
        self._make_script().test()

    def test_benchmark_noop(self) -> None:
        self._make_script().benchmark()

    def test_clean_noop(self) -> None:
        self._make_script().clean()


class TestZScriptReleaseDefault(unittest.TestCase):
    """Default ``ZScript.release()`` packages ``.nanvix/out/release``.

    The hook is a thin wrapper around :func:`nanvix_zutil.release.package`;
    exhaustive coverage of archive contents, format handling, and input
    validation lives in ``tests/test_release.py``. These tests only verify
    the wiring: the release directory is picked up, archives land in the
    dist directory under the manifest name, and a missing release directory
    fails cleanly.
    """

    def setUp(self) -> None:
        write_manifest()  # manifest name = "test"

    def _populate_release_dir(self) -> Path:
        rel = paths.release_dir()
        rel.mkdir(parents=True, exist_ok=True)
        (rel / "artifact.bin").write_bytes(b"payload")
        return rel

    def test_packages_when_release_dir_exists(self) -> None:
        """With a populated release dir, archives appear in dist_dir()."""
        self._populate_release_dir()

        ZScript().release()

        dist = paths.dist_dir()
        produced = {p.name for p in dist.iterdir()}
        # Manifest name is "test"; DEFAULT_FORMATS = tar.gz + zip.
        self.assertEqual(produced, {"test.tar.gz", "test.zip"})
        for p in dist.iterdir():
            self.assertGreater(p.stat().st_size, 0, f"empty archive: {p}")

    def test_emits_success_message(self) -> None:
        """A human-readable success line is written to stderr."""
        self._populate_release_dir()

        buf = StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            ZScript().release()
        finally:
            sys.stderr = original_stderr

        output = buf.getvalue()
        self.assertIn("success:", output)
        self.assertIn("Packaged 2 archive(s) for 'test'", output)
        self.assertIn(str(paths.dist_dir()), output)

    def test_fails_when_release_dir_missing(self) -> None:
        """Missing ``.nanvix/out/release`` aborts with EXIT_GENERAL_ERROR."""
        from nanvix_zutil.exitcodes import EXIT_GENERAL_ERROR

        self.assertFalse(paths.release_dir().exists())

        with self.assertRaises(SystemExit) as ctx:
            ZScript().release()
        self.assertEqual(ctx.exception.code, EXIT_GENERAL_ERROR)

        # Nothing should have been produced in dist/.
        dist = paths.dist_dir()
        if dist.exists():
            self.assertEqual(list(dist.iterdir()), [])

    def test_failure_emits_error_with_hint(self) -> None:
        """The failure path surfaces a recognizable error + hint on stderr."""
        buf = StringIO()
        original_stderr = sys.stderr
        sys.stderr = buf
        try:
            with self.assertRaises(SystemExit):
                ZScript().release()
        finally:
            sys.stderr = original_stderr

        output = buf.getvalue()
        self.assertIn("error:", output)
        self.assertIn(str(paths.release_dir()), output)
        self.assertIn("hint:", output)
        self.assertIn("release", output)


class TestZScriptAvailableSubcommands(unittest.TestCase):
    """available_subcommands() reflects hook overrides."""

    def setUp(self) -> None:
        write_manifest()

    def test_base_class_exposes_only_auto_hooks(self) -> None:
        script = ZScript()
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

        script = _Sub()
        available = script.available_subcommands()
        self.assertIn("build", available)
        self.assertIn("test", available)

    def test_subclass_hides_non_overridden_hooks(self) -> None:
        class _Sub(ZScript):
            def build(self) -> None:
                pass

        script = _Sub()
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

        script = _FullSub()
        available = script.available_subcommands()
        for hook in ZScript.CONSUMER_HOOKS:
            self.assertIn(hook, available)


class TestHelpersRun(unittest.TestCase):
    """helpers.run() executes subprocesses correctly."""

    def setUp(self) -> None:
        write_manifest()

    def test_run_success(self) -> None:
        result = helpers.run(sys.executable, "-c", "print('ok')")
        self.assertEqual(result.returncode, 0)

    def test_run_failure_exits(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            helpers.run(sys.executable, "-c", "raise SystemExit(1)")
        self.assertEqual(ctx.exception.code, EXIT_BUILD_FAILURE)

    def test_run_timeout_exits(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            helpers.run(sys.executable, "-c", "import time; time.sleep(10)", timeout=1)
        self.assertEqual(ctx.exception.code, EXIT_BUILD_FAILURE)

    def test_run_without_docker_uses_args_directly(self) -> None:
        """Without a docker config, run() executes the command as-is."""
        result = helpers.run(sys.executable, "-c", "print('ok')")
        self.assertEqual(result.returncode, 0)

    def _make_docker(self, repo_root: Path) -> DockerConfig:
        return DockerConfig(
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
            mounts=[
                Mount(
                    host_path=repo_root,
                    container_path=WORKSPACE_CONTAINER_PATH,
                )
            ],
            uid=1000,
            gid=1000,
        )

    @patch("nanvix_zutil.helpers.is_windows", return_value=True)
    def test_run_uses_windows_cmd_on_windows(self, _mock: object) -> None:
        """run() delegates to build_windows_run_cmd on Windows."""
        docker = self._make_docker(Path.cwd())
        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.run("make", "all", docker=docker)

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # Should use sh -c (Windows tar-copy mode).
        self.assertIn("sh", cmd)
        self.assertIn("-c", cmd)

    @patch("nanvix_zutil.helpers.is_windows", return_value=True)
    def test_run_dispatch_windows_default_docker(self, _mock: object) -> None:
        """run() uses build_windows_run_cmd on Windows even with default
        (empty) output_files — the dispatch no longer requires
        these fields to be populated."""
        docker = DockerConfig(
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
            mounts=[
                Mount(
                    host_path=Path.cwd(),
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

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.run("make", "all", docker=docker)

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # Should still use sh -c (Windows tar-copy mode) even without
        # output_files.
        self.assertIn("sh", cmd)
        self.assertIn("-c", cmd)

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_run_dispatch_linux_uses_build_run_cmd(self, *_mocks: object) -> None:
        """run() uses build_run_cmd on Linux (not the Windows tar-copy path)."""
        docker = self._make_docker(Path.cwd())
        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.run("make", "all", docker=docker)

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # Standard docker run — should NOT use sh -c wrapping.
        self.assertEqual(cmd[:3], ["docker", "run", "--rm"])
        # Inner command is appended directly (not wrapped in sh -c).
        self.assertEqual(cmd[-2:], ["make", "all"])
        self.assertNotEqual(cmd[-3:-1], ["sh", "-c"])

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_run_with_docker_wraps_command(self, *_mocks: object) -> None:
        """When a docker config is supplied, run() prepends docker run."""
        docker = self._make_docker(Path.cwd())
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.run("make", "all", docker=docker)

        self.assertTrue(captured)
        self.assertIn("docker", captured[0])
        self.assertIn("make", captured[0])
        self.assertIn("all", captured[0])

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_run_env_forwarded_into_container(self, *_mocks: object) -> None:
        """env vars passed to run() are forwarded as -e flags in Docker mode."""
        docker = DockerConfig(
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
            mounts=[],
            uid=1000,
            gid=1000,
        )
        captured_kwargs: list[dict[str, object]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_kwargs.append(dict(kwargs))
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.run("make", "all", docker=docker, env={"MY_VAR": "hello"})

        self.assertTrue(captured_kwargs)
        # env should NOT be passed to the docker subprocess itself
        self.assertIsNone(captured_kwargs[0].get("env"))

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_run_env_forwarded_into_container_as_flags(self, *_mocks: object) -> None:
        """env vars appear as -e KEY=VAL in the docker run command."""
        docker = DockerConfig(
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
            mounts=[],
            uid=1000,
            gid=1000,
        )
        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.run("make", "all", docker=docker, env={"MY_VAR": "hello"})

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # -e MY_VAR=hello must appear in the docker run command
        self.assertIn("-e", cmd)
        self.assertIn("MY_VAR=hello", cmd)

    @patch.dict(
        os.environ,
        {"USER": "test-runner-user", "USERNAME": "test-runner-user"},
        clear=False,
    )
    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_run_strips_blocklisted_env_vars(self, *_mocks: object) -> None:
        """helpers.run() must not forward host-only env vars
        (PATH/HOME/USER/...) into the container, including mixed-case
        variants.  Non-blocklisted keys still pass through."""
        docker = DockerConfig(
            image="ghcr.io/nanvix/toolchain-gcc:sha-34a3641",
            mounts=[],
            uid=1000,
            gid=1000,
        )
        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        env = {
            # Blocklisted (uppercase canonical).
            "PATH": "C:\\Windows\\System32",
            "HOME": "/home/host",
            "USER": "host-user",
            "USERNAME": "host-user",
            "PWD": "/some/host/path",
            "OLDPWD": "/other/host/path",
            "SHELL": "/bin/bash",
            "TERM": "xterm",
            "LD_LIBRARY_PATH": "/host/lib",
            "DYLD_LIBRARY_PATH": "/host/dyld",
            "PYTHONPATH": "/host/py",
            "PYTHONHOME": "/host/pyhome",
            # Blocklisted (mixed case).
            "Path": "C:\\mixed",
            "home": "/home/lower",
            # Non-blocklisted — must pass through.
            "CC": "gcc",
            "CFLAGS": "-O2",
            "PKG_CONFIG_PATH": "/usr/local/lib/pkgconfig",
        }

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.run("make", "all", docker=docker, env=env)

        self.assertTrue(captured_cmds)
        cmd = captured_cmds[0]
        # Collect every "KEY=VALUE" token that follows a "-e" flag.
        forwarded: list[tuple[str, str]] = []
        for i, tok in enumerate(cmd):
            if tok == "-e" and i + 1 < len(cmd):
                key, _, val = cmd[i + 1].partition("=")
                forwarded.append((key, val))

        # The caller's host-only values must never reach the container.
        # ``DockerConfig`` legitimately sets ``HOME``/``USER`` itself, so we
        # check VALUES rather than keys for those cases.
        host_values = {
            "PATH": "C:\\Windows\\System32",
            "HOME": "/home/host",
            "USER": "host-user",
            "USERNAME": "host-user",
            "PWD": "/some/host/path",
            "OLDPWD": "/other/host/path",
            "SHELL": "/bin/bash",
            "TERM": "xterm",
            "LD_LIBRARY_PATH": "/host/lib",
            "DYLD_LIBRARY_PATH": "/host/dyld",
            "PYTHONPATH": "/host/py",
            "PYTHONHOME": "/host/pyhome",
            "Path": "C:\\mixed",
            "home": "/home/lower",
        }
        for key, host_val in host_values.items():
            self.assertNotIn(
                (key, host_val),
                forwarded,
                f"blocklisted env var {key!r}={host_val!r} leaked into docker run",
            )

        # Non-blocklisted keys must still pass through with their values.
        for allowed_key, allowed_val in (
            ("CC", "gcc"),
            ("CFLAGS", "-O2"),
            ("PKG_CONFIG_PATH", "/usr/local/lib/pkgconfig"),
        ):
            self.assertIn(
                (allowed_key, allowed_val),
                forwarded,
                f"non-blocklisted env var {allowed_key!r} missing from docker run",
            )


class TestZScriptSysrootRequiredFiles(unittest.TestCase):
    """sysroot_required_files() varies by deployment mode."""

    def setUp(self) -> None:
        write_manifest()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_multi_process_includes_linuxd_and_uservm(self) -> None:
        os.environ["NANVIX_DEPLOYMENT_MODE"] = "multi-process"
        script = ZScript()
        files = script.sysroot_required_files()
        self.assertIn("bin/linuxd.elf", files)
        self.assertIn("bin/uservm.elf", files)

    def test_single_process_excludes_linuxd_and_uservm(self) -> None:
        os.environ["NANVIX_DEPLOYMENT_MODE"] = "single-process"
        script = ZScript()
        files = script.sysroot_required_files()
        self.assertNotIn("bin/linuxd.elf", files)
        self.assertNotIn("bin/uservm.elf", files)

    def test_standalone_excludes_linuxd_and_uservm(self) -> None:
        os.environ["NANVIX_DEPLOYMENT_MODE"] = "standalone"
        script = ZScript()
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
            script = ZScript()
            files = script.sysroot_required_files()
            self.assertIn("lib/libposix.a", files, f"missing in {mode}")
            self.assertIn("lib/user.ld", files, f"missing in {mode}")
            self.assertIn(nanvixd, files, f"missing in {mode}")
            self.assertIn("bin/kernel.elf", files, f"missing in {mode}")
            self.assertIn(mkramfs, files, f"missing in {mode}")

    def test_default_deployment_mode_is_standalone(self) -> None:
        """Default (no env override) should be standalone."""
        script = ZScript()
        files = script.sysroot_required_files()
        self.assertNotIn("bin/linuxd.elf", files)
        self.assertNotIn("bin/uservm.elf", files)


class TestZScriptDockerConfig(unittest.TestCase):
    """Tests for ZScript.docker / ZScript.docker_config()."""

    def setUp(self) -> None:
        write_manifest()

    def _make_script(self) -> ZScript:
        return ZScript()

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
        self.assertEqual(workspace_mount.host_path, paths.repo_root())

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
        buildroot_dir = paths.buildroot()
        buildroot_dir.mkdir(parents=True, exist_ok=True)
        cfg = script.docker_config("test-image")
        buildroot_mount = next(
            (m for m in cfg.mounts if m.container_path == BUILDROOT_CONTAINER_PATH),
            None,
        )
        self.assertIsNotNone(buildroot_mount)
        assert buildroot_mount is not None
        self.assertEqual(buildroot_mount.host_path, buildroot_dir)
        self.assertFalse(buildroot_mount.readonly)


class TestZScriptAutoDocker(unittest.TestCase):
    """Docker is always enabled for setup/build/release/clean (hard fail)."""

    def setUp(self) -> None:
        write_manifest()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

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
        nanvix_dir = paths.nanvix_root()
        (nanvix_dir / "env.json").write_text(
            '{"NANVIX_DOCKER_IMAGE": "ghcr.io/nanvix/toolchain-gcc:sha-34a3641"}'
        )

        with (
            patch("sys.argv", ["z.py", "build"]),
            patch("nanvix_zutil.script.is_windows", return_value=True),
            patch.object(BuildScript, "build", _fake_build),
            patch("nanvix_zutil.script.log"),
        ):
            BuildScript.main()

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
        nanvix_dir = paths.nanvix_root()
        (nanvix_dir / "env.json").write_text(
            '{"NANVIX_DOCKER_IMAGE": "ghcr.io/nanvix/toolchain-gcc:sha-34a3641"}'
        )

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("sys.argv", ["z.py", "build"]),
            patch("nanvix_zutil.script.is_windows", return_value=False),
            patch("nanvix_zutil.helpers.shutil.which", return_value="/usr/bin/docker"),
            patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
            patch.object(BuildScript, "build", _fake_build),
            patch("nanvix_zutil.script.log"),
        ):
            BuildScript.main()

        self.assertTrue(docker_configured)


class TestZScriptCleanWindows(unittest.TestCase):
    """ZScript.clean() Windows behavior."""

    def setUp(self) -> None:
        write_manifest()

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_clean_removes_configured_artifact(self, _mock: object) -> None:
        """clean() removes .nanvix-configured on Windows."""
        script = ZScript()
        artifact = paths.repo_root() / ".nanvix-configured"
        artifact.write_text("marker")
        script.clean()
        self.assertFalse(artifact.exists())

    @patch("nanvix_zutil.script.is_windows", return_value=True)
    def test_clean_noop_when_no_artifacts(self, _mock: object) -> None:
        """clean() does not raise when no artifacts exist on Windows."""
        script = ZScript()
        script.clean()  # Should not raise.

    @patch("nanvix_zutil.script.is_windows", return_value=False)
    def test_clean_noop_on_linux(self, _mock: object) -> None:
        """clean() is a no-op on Linux (base class)."""
        script = ZScript()
        script.clean()  # Should not raise.


class TestZScriptSetupFallbackReporting(unittest.TestCase):
    """setup() reports fallback state correctly."""

    def setUp(self) -> None:
        write_manifest(MANIFEST_WITH_DEPS)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

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
            script = ZScript()
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
            script = ZScript()
            result = script.setup()

        self.assertTrue(result)

    def test_setup_no_deps_returns_false(self) -> None:
        """setup() returns False when there are no dependencies."""
        write_manifest()  # default manifest, no deps

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript()
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
            script = ZScript()
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
        write_manifest(MANIFEST_WITH_DEPS)
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

        # script.py calls helpers.check_docker (imported into the script
        # module namespace). Patch it to a no-op so these tests don't try to
        # actually pull a Docker image.
        p = patch("nanvix_zutil.script.check_docker", return_value=None)
        p.start()
        self.addCleanup(p.stop)

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
            patch.object(ZScript, "setup", _setup_with_fallback),
            self.assertRaises(SystemExit) as ctx,
        ):
            ZScript.main()

        self.assertEqual(ctx.exception.code, EXIT_DEGRADED_SETUP)

    def test_main_exits_7_on_return_only_fallback_setup(self) -> None:
        """main() honors setup() returning True without touching private state."""
        from nanvix_zutil.exitcodes import EXIT_DEGRADED_SETUP

        with (
            patch("sys.argv", ["z.py", "setup", "--with-docker", "test/image:tag"]),
            patch.object(ZScript, "setup", return_value=True),
            self.assertRaises(SystemExit) as ctx,
        ):
            ZScript.main()

        self.assertEqual(ctx.exception.code, EXIT_DEGRADED_SETUP)

    def test_main_exits_0_on_clean_setup(self) -> None:
        """main() with setup subcommand completes normally when no fallback."""
        with (
            patch("sys.argv", ["z.py", "setup", "--with-docker", "test/image:tag"]),
            patch.object(ZScript, "setup", return_value=False),
            patch("nanvix_zutil.script.log") as mock_log,
        ):
            # Should not raise SystemExit.
            ZScript.main()

        # Verify success log was emitted.
        success_calls = [
            call
            for call in mock_log.success.call_args_list
            if "complete" in str(call).lower()
        ]
        self.assertTrue(success_calls, "Expected a success log on clean setup")

    def test_main_fatal_exits_with_degraded_code(self) -> None:
        """main() exits with EXIT_DEGRADED_SETUP when setup() returns True."""
        from nanvix_zutil.exitcodes import EXIT_DEGRADED_SETUP

        with (
            patch(
                "sys.argv",
                ["z.py", "setup", "--with-docker", "test/image:tag"],
            ),
            patch.object(ZScript, "setup", return_value=True),
            self.assertRaises(SystemExit) as ctx,
        ):
            ZScript.main()

        self.assertEqual(ctx.exception.code, EXIT_DEGRADED_SETUP)


class TestZScriptSetupWithNanvix(unittest.TestCase):
    """setup() with --with-nanvix overlays local artifacts."""

    def setUp(self) -> None:
        write_manifest()
        for key in (
            "NANVIX_MACHINE",
            "NANVIX_DEPLOYMENT_MODE",
            "NANVIX_MEMORY_SIZE",
        ):
            os.environ.pop(key, None)

    def test_setup_calls_overlay_when_path_set(self) -> None:
        """setup() calls sysroot.overlay_local_nanvix when --with-nanvix is set."""
        local_dir = Path.cwd() / "local-nanvix"
        local_dir.mkdir()

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript()
            script._with_nanvix_path = str(local_dir)
            script.setup()

        fake_sysroot.overlay_local_nanvix.assert_called_once_with(local_dir)
        fake_sysroot.verify.assert_called_once()

    def test_setup_no_overlay_without_path(self) -> None:
        """setup() does not call overlay_local_nanvix when --with-nanvix is unset."""
        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")

        with patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot):
            script = ZScript()
            script.setup()

        fake_sysroot.overlay_local_nanvix.assert_not_called()

    def test_setup_local_deps_skips_github(self) -> None:
        """setup() skips GitHub download for deps found locally."""
        write_manifest(MANIFEST_WITH_DEPS)

        local_dir = Path.cwd() / "local-nanvix"
        (local_dir / "deps" / "zlib" / "lib").mkdir(parents=True)
        (local_dir / "deps" / "zlib" / "lib" / "libz.a").write_bytes(b"local-zlib")

        fake_sysroot = MagicMock()
        fake_sysroot.path = Path("/fake/sysroot")
        fake_sysroot.tag = "v0.1.0"

        with (
            patch("nanvix_zutil.script.Sysroot.download", return_value=fake_sysroot),
            patch("nanvix_zutil.script.resolve_release_with_fallback") as mock_resolve,
        ):
            script = ZScript()
            script._with_nanvix_path = str(local_dir)
            script.setup()

        # The dependency was satisfied locally so GitHub resolve should not
        # have been called.
        mock_resolve.assert_not_called()


class TestZScriptSetupLocalSysroot(unittest.TestCase):
    """setup() with RefKind.LOCAL sysroot uses from_local, no GitHub."""

    def setUp(self) -> None:
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_local_sysroot_skips_github(self) -> None:
        """When sysroot ref is LOCAL, Sysroot.from_local is used."""
        repo_root = paths.repo_root()
        # Create a local sysroot directory.
        local_sysroot = repo_root / "my-sysroot"
        local_sysroot.mkdir()

        write_manifest()

        script = ZScript()
        # Simulate a manifest with a LOCAL sysroot ref.
        from nanvix_zutil.buildroot import Ref

        script.manifest.sysroot_ref = Ref(kind=RefKind.LOCAL, value=str(local_sysroot))

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

    def test_local_sysroot_via_cli_sysroot_path(self) -> None:
        """When --sysroot-path is provided, Sysroot.from_local is used."""
        repo_root = paths.repo_root()
        local_sysroot = repo_root / "my-sysroot"
        local_sysroot.mkdir()

        write_manifest()

        script = ZScript()
        script._cli_sysroot_path = str(local_sysroot)

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


class TestHelpersMakeInitrd(unittest.TestCase):
    """helpers.make_initrd() builds the correct mkimage command."""

    def setUp(self) -> None:
        write_manifest()

    def _make_script(self) -> ZScript:
        script = ZScript()
        # Set up a fake sysroot with a bin/ directory and mkimage stub.
        sysroot_bin = paths.sysroot() / "bin"
        sysroot_bin.mkdir(parents=True, exist_ok=True)
        (sysroot_bin / "mkimage.elf").touch()
        (sysroot_bin / "mkimage.exe").touch()
        fake_sysroot = MagicMock()
        fake_sysroot.path = paths.sysroot()
        script.sysroot = fake_sysroot
        return script

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_basic_invocation_linux(self, _mock: object) -> None:
        """Produces the expected command on Linux."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            result = helpers.make_initrd(
                script, "my-app.elf", test=False, args=InitRdArgs()
            )

        self.assertEqual(result, paths.bin_out() / "my-app.img")
        cmd = captured[0]
        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(cmd[0], str(bin_dir / "mkimage.elf"))
        self.assertEqual(cmd[1], "-o")
        self.assertEqual(cmd[2], str(paths.bin_out() / "my-app.img"))
        self.assertEqual(cmd[3], f"{bin_dir / 'procd.elf'};procd")
        self.assertEqual(cmd[4], f"{bin_dir / 'memd.elf'};memd")
        self.assertEqual(cmd[5], f"{bin_dir / 'vfsd.elf'};vfsd")
        self.assertEqual(cmd[6], f"{paths.repo_root() / 'my-app.elf'};my-app.elf")

    @patch("nanvix_zutil.helpers.is_windows", return_value=True)
    def test_basic_invocation_windows(self, _mock: object) -> None:
        """Uses mkimage.exe on Windows."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(script, "my-app.elf", test=False, args=InitRdArgs())

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(captured[0][0], str(bin_dir / "mkimage.exe"))

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_app_args(self, _mock: object) -> None:
        """App arguments are appended to the app entry."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(app_args=["--verbose", "--port=8080"]),
            )

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry,
            f"{paths.repo_root() / 'my-app.elf'};my-app.elf --verbose --port=8080",
        )

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_daemon_args(self, _mock: object) -> None:
        """Daemon arguments are appended to respective entries."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(
                    procd_args=["--debug"],
                    memd_args=["--heap=64m"],
                    vfsd_args=["--cache=off"],
                ),
            )

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(captured[0][3], f"{bin_dir / 'procd.elf'};procd --debug")
        self.assertEqual(captured[0][4], f"{bin_dir / 'memd.elf'};memd --heap=64m")
        self.assertEqual(captured[0][5], f"{bin_dir / 'vfsd.elf'};vfsd --cache=off")

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_kernel_args(self, _mock: object) -> None:
        """Kernel arguments are passed via --kernel-args."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(kernel_args=["console=ttyS0", "debug"]),
            )

        cmd = captured[0]
        # -kernel-args should appear after -o <output>
        ka_idx = cmd.index("-kernel-args")
        self.assertEqual(cmd[ka_idx + 1], "console=ttyS0 debug")

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_semicolons_escaped_in_args(self, _mock: object) -> None:
        """Semicolons in arguments are escaped as \\;."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script, "my-app.elf", test=False, args=InitRdArgs(app_args=["--sep=;"])
            )

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry, f"{paths.repo_root() / 'my-app.elf'};my-app.elf --sep=\\;"
        )

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_semicolons_escaped_in_kernel_args(self, _mock: object) -> None:
        """Semicolons in kernel arguments are escaped."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script, "my-app.elf", test=False, args=InitRdArgs(kernel_args=["a;b"])
            )

        cmd = captured[0]
        ka_idx = cmd.index("-kernel-args")
        self.assertEqual(cmd[ka_idx + 1], "a\\;b")

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_custom_bin_dir(self, _mock: object) -> None:
        """A custom bin_dir is used instead of the sysroot."""
        script = self._make_script()
        custom_bin = Path.cwd() / "custom" / "bin"
        custom_bin.mkdir(parents=True)
        (custom_bin / "mkimage.elf").touch()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script, "my-app.elf", test=False, args=InitRdArgs(bin_dir=custom_bin)
            )

        self.assertEqual(captured[0][0], str(custom_bin / "mkimage.elf"))
        self.assertIn(str(custom_bin / "procd.elf"), captured[0][3])

    def test_no_sysroot_exits(self) -> None:
        """Exits with EXIT_MISSING_DEP when sysroot is None and config lacks one."""
        script = ZScript()
        script.sysroot = None
        with self.assertRaises(SystemExit) as ctx:
            helpers.make_initrd(script, "my-app.elf", test=False, args=InitRdArgs())
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_app_stem_derived_from_filename(self, _mock: object) -> None:
        """The output .img uses the stem; argv0 uses the full filename."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            result = helpers.make_initrd(
                script, "hello-world.elf", test=False, args=InitRdArgs()
            )

        self.assertEqual(result, paths.bin_out() / "hello-world.img")
        self.assertEqual(
            captured[0][6], f"{paths.repo_root() / 'hello-world.elf'};hello-world.elf"
        )

    def test_app_with_path_separator_exits(self) -> None:
        """Exits with EXIT_MISSING_DEP when app contains path separators."""
        script = self._make_script()
        with self.assertRaises(SystemExit) as ctx:
            helpers.make_initrd(
                script, "build/hello.elf", test=False, args=InitRdArgs()
            )
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_mkimage_not_found_exits(self, _mock: object) -> None:
        """Exits with EXIT_MISSING_DEP when mkimage binary is missing."""
        script = ZScript()
        # Sysroot bin dir exists but mkimage.elf does not.
        sysroot_bin = paths.sysroot() / "bin"
        sysroot_bin.mkdir(parents=True, exist_ok=True)
        fake_sysroot = MagicMock()
        fake_sysroot.path = paths.sysroot()
        script.sysroot = fake_sysroot
        with self.assertRaises(SystemExit) as ctx:
            helpers.make_initrd(script, "my-app.elf", test=False, args=InitRdArgs())
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_app_env(self, _mock: object) -> None:
        """Environment variables are appended after a semicolon separator."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(app_env=["VAR1=foo", "VAR2=bar"]),
            )

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry,
            f"{paths.repo_root() / 'my-app.elf'};my-app.elf;VAR1=foo VAR2=bar",
        )

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_app_args_and_env(self, _mock: object) -> None:
        """Both app arguments and environment variables are emitted."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(app_args=["--verbose"], app_env=["DEBUG=1"]),
            )

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry,
            f"{paths.repo_root() / 'my-app.elf'};my-app.elf --verbose;DEBUG=1",
        )

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_daemon_env(self, _mock: object) -> None:
        """Daemon environment variables are appended to respective entries."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(
                    procd_env=["LOG=debug"],
                    memd_env=["HEAP=64m"],
                    vfsd_env=["CACHE=off"],
                ),
            )

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(captured[0][3], f"{bin_dir / 'procd.elf'};procd;LOG=debug")
        self.assertEqual(captured[0][4], f"{bin_dir / 'memd.elf'};memd;HEAP=64m")
        self.assertEqual(captured[0][5], f"{bin_dir / 'vfsd.elf'};vfsd;CACHE=off")

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_env_semicolons_escaped(self, _mock: object) -> None:
        """Semicolons in env values are escaped."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(app_env=["PATH=/a;/b"]),
            )

        app_entry = captured[0][6]
        self.assertEqual(
            app_entry,
            f"{paths.repo_root() / 'my-app.elf'};my-app.elf;PATH=/a\\;/b",
        )

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_daemon_args_and_env(self, _mock: object) -> None:
        """Daemon entries include both CLI arguments and environment variables."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(
                    procd_args=["--log-level", "trace"], procd_env=["LOG=debug"]
                ),
            )

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(
            captured[0][3],
            f"{bin_dir / 'procd.elf'};procd --log-level trace;LOG=debug",
        )

    @patch("nanvix_zutil.helpers.is_windows", return_value=True)
    def test_env_windows(self, _mock: object) -> None:
        """Environment variables work correctly on Windows (mkimage.exe)."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            helpers.make_initrd(
                script,
                "my-app.elf",
                test=False,
                args=InitRdArgs(
                    app_args=["--verbose"], app_env=["DEBUG=1"], procd_env=["LOG=debug"]
                ),
            )

        bin_dir = script.sysroot.path / "bin"  # type: ignore[union-attr]
        self.assertEqual(captured[0][0], str(bin_dir / "mkimage.exe"))
        self.assertEqual(captured[0][3], f"{bin_dir / 'procd.elf'};procd;LOG=debug")
        self.assertEqual(
            captured[0][6],
            f"{paths.repo_root() / 'my-app.elf'};my-app.elf --verbose;DEBUG=1",
        )

    @patch("nanvix_zutil.helpers.is_windows", return_value=False)
    def test_output_location_depends_on_test_flag(self, _mock: object) -> None:
        """Output directory is ``test_out()`` when ``test=True`` and
        ``bin_out()`` when ``test=False``; in both cases the directory is
        created automatically if it does not already exist."""
        script = self._make_script()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            captured.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        # Sanity: neither output directory should exist before the calls
        # so we can verify make_initrd creates them.
        self.assertFalse(paths.bin_out().exists())
        self.assertFalse(paths.test_out().exists())

        with patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run):
            release = helpers.make_initrd(
                script, "my-app.elf", test=False, args=InitRdArgs()
            )
            test_img = helpers.make_initrd(
                script, "my-app.elf", test=True, args=InitRdArgs()
            )

        self.assertEqual(release, paths.bin_out() / "my-app.img")
        self.assertEqual(test_img, paths.test_out() / "my-app.img")
        self.assertTrue(paths.bin_out().is_dir())
        self.assertTrue(paths.test_out().is_dir())
        # The mkimage command's ``-o`` argument should reflect the chosen
        # output directory for each invocation.
        self.assertEqual(captured[0][2], str(paths.bin_out() / "my-app.img"))
        self.assertEqual(captured[1][2], str(paths.test_out() / "my-app.img"))


class TestHelpersCheckDocker(unittest.TestCase):
    """helpers.check_docker() probes for the image and auto-pulls when missing."""

    def setUp(self) -> None:
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_missing_docker_binary_exits_fatal(self) -> None:
        """No `docker` on PATH -> fatal EXIT_MISSING_DEP, no subprocess calls."""
        mock_run = MagicMock()
        with (
            patch("nanvix_zutil.helpers.shutil.which", return_value=None),
            patch("nanvix_zutil.helpers.subprocess.run", mock_run),
            self.assertRaises(SystemExit) as ctx,
        ):
            helpers.check_docker("test/image:tag")
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)
        mock_run.assert_not_called()

    def test_image_present_skips_pull(self) -> None:
        """`docker image inspect` returns 0 -> no pull, no fatal."""
        image = "test/image:tag"

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            calls.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("nanvix_zutil.helpers.shutil.which", return_value="/usr/bin/docker"),
            patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
        ):
            helpers.check_docker(image)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ["docker", "image", "inspect", image])

    def test_image_missing_pull_succeeds(self) -> None:
        """`image inspect` fails, `docker pull` succeeds -> no fatal."""
        image = "test/image:tag"

        calls: list[list[str]] = []
        returncodes = iter([1, 0])  # inspect: miss, pull: ok

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            calls.append(cmd)
            return sp.CompletedProcess(
                args=cmd, returncode=next(returncodes), stdout="", stderr=""
            )

        with (
            patch("nanvix_zutil.helpers.shutil.which", return_value="/usr/bin/docker"),
            patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
        ):
            helpers.check_docker(image)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], ["docker", "image", "inspect", image])
        self.assertEqual(calls[1], ["docker", "pull", image])

    def test_image_missing_pull_fails_exits_fatal(self) -> None:
        """`image inspect` fails, `docker pull` fails -> fatal EXIT_MISSING_DEP."""
        image = "test/image:tag"

        calls: list[list[str]] = []
        returncodes = iter([1, 1])  # inspect: miss, pull: fail

        def fake_run(cmd: list[str], **kwargs: object) -> sp.CompletedProcess[str]:
            calls.append(cmd)
            return sp.CompletedProcess(
                args=cmd, returncode=next(returncodes), stdout="", stderr=""
            )

        with (
            patch(
                "nanvix_zutil.helpers.shutil.which",
                return_value="/usr/bin/docker",
            ),
            patch("nanvix_zutil.helpers.subprocess.run", side_effect=fake_run),
            self.assertRaises(SystemExit) as ctx,
        ):
            helpers.check_docker(image)
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], ["docker", "image", "inspect", image])
        self.assertEqual(calls[1], ["docker", "pull", image])


class TestOfflineMode(unittest.TestCase):
    """Tests for offline mode (--offline flag)."""

    def setUp(self) -> None:
        write_manifest(MANIFEST_WITH_DEPS)
        for key in (
            "NANVIX_MACHINE",
            "NANVIX_DEPLOYMENT_MODE",
            "NANVIX_MEMORY_SIZE",
        ):
            os.environ.pop(key, None)

    def test_offline_default_false(self) -> None:
        """Offline mode defaults to False."""
        script = ZScript()
        self.assertFalse(script._offline)

    def test_with_nanvix_path_initially_none(self) -> None:
        """_with_nanvix_path starts as None."""
        script = ZScript()
        self.assertIsNone(script._with_nanvix_path)

    def test_cli_sysroot_path_initially_none(self) -> None:
        """_cli_sysroot_path starts as None."""
        script = ZScript()
        self.assertIsNone(script._cli_sysroot_path)

    def test_offline_with_sysroot_path_uses_from_local(self) -> None:
        """In offline mode with --sysroot-path, Sysroot.from_local is used."""
        sysroot_dir = paths.repo_root() / "my-sysroot"
        sysroot_dir.mkdir()

        script = ZScript()
        script._offline = True
        script._cli_sysroot_path = str(sysroot_dir)
        script._with_nanvix_path = str(paths.repo_root())

        fake_sysroot = MagicMock()
        fake_sysroot.path = sysroot_dir
        fake_sysroot.tag = ""

        with (
            patch("nanvix_zutil.script.Sysroot.download") as mock_download,
            patch(
                "nanvix_zutil.script.Sysroot.from_local",
                return_value=fake_sysroot,
            ) as mock_from_local,
        ):
            script.setup()
            mock_download.assert_not_called()
            mock_from_local.assert_called_once()

    def test_offline_without_sysroot_exits(self) -> None:
        """Offline mode without a local sysroot path exits fatally."""
        script = ZScript()
        script._offline = True

        with self.assertRaises(SystemExit) as ctx:
            script.setup()

        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)

    def test_offline_without_with_nanvix_exits(self) -> None:
        """Offline mode with sysroot but no --with-nanvix exits fatally."""
        repo_root = paths.repo_root()
        sysroot_dir = repo_root / "my-sysroot"
        sysroot_dir.mkdir()

        script = ZScript()
        script._offline = True
        script._cli_sysroot_path = str(sysroot_dir)

        fake_sysroot = MagicMock()
        fake_sysroot.path = sysroot_dir
        fake_sysroot.tag = ""

        with (
            patch(
                "nanvix_zutil.script.Sysroot.from_local",
                return_value=fake_sysroot,
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            script.setup()

        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)

    def test_offline_missing_dep_warns_not_fatal(self) -> None:
        """Offline mode warns (not fatal) when a dep has no local artifacts."""
        repo_root = paths.repo_root()
        sysroot_dir = repo_root / "my-sysroot"
        sysroot_dir.mkdir()
        # Create --with-nanvix dir without deps
        build_dir = repo_root / "build"
        build_dir.mkdir()

        script = ZScript()
        script._offline = True
        script._cli_sysroot_path = str(sysroot_dir)
        script._with_nanvix_path = str(build_dir)

        fake_sysroot = MagicMock()
        fake_sysroot.path = sysroot_dir
        fake_sysroot.tag = ""

        with patch(
            "nanvix_zutil.script.Sysroot.from_local",
            return_value=fake_sysroot,
        ):
            # Should NOT raise SystemExit — just warn
            script.setup()

    def test_offline_with_local_dep_installs(self) -> None:
        """Offline mode installs dep from local path when artifacts exist."""
        repo_root = paths.repo_root()
        sysroot_dir = repo_root / "my-sysroot"
        sysroot_dir.mkdir()
        build_dir = repo_root / "build"
        (build_dir / "deps" / "zlib" / "lib").mkdir(parents=True)
        (build_dir / "deps" / "zlib" / "lib" / "libz.a").write_bytes(b"fake")

        script = ZScript()
        script._offline = True
        script._cli_sysroot_path = str(sysroot_dir)
        script._with_nanvix_path = str(build_dir)

        fake_sysroot = MagicMock()
        fake_sysroot.path = sysroot_dir
        fake_sysroot.tag = ""

        with (
            patch(
                "nanvix_zutil.script.Sysroot.from_local",
                return_value=fake_sysroot,
            ),
            patch("nanvix_zutil.script.resolve_release") as mock_resolve,
        ):
            script.setup()

        # GitHub resolve should NOT be called in offline mode
        mock_resolve.assert_not_called()
        # Dep should be installed in buildroot
        self.assertIsNotNone(script.buildroot)
        buildroot_lib = paths.buildroot() / "lib" / "libz.a"  # type: ignore[union-attr]
        self.assertTrue(buildroot_lib.exists())


class TestInstallArtifacts(unittest.TestCase):
    """Tests for ZScript.install_artifacts()."""

    def setUp(self) -> None:
        write_manifest()
        for key in ("NANVIX_MACHINE", "NANVIX_DEPLOYMENT_MODE", "NANVIX_MEMORY_SIZE"):
            os.environ.pop(key, None)

    def test_exports_from_output_dir(self) -> None:
        """install_artifacts copies from .nanvix/out/."""
        repo_root = paths.repo_root()
        script = ZScript()

        # Create .nanvix/out/{lib,include}/
        output_src = paths.out_dir()
        (output_src / "lib").mkdir(parents=True)
        (output_src / "lib" / "libfoo.a").write_bytes(b"lib")
        (output_src / "include").mkdir(parents=True)
        (output_src / "include" / "foo.h").write_text("#pragma once")

        target = repo_root / "export"
        script.install_artifacts(str(target))

        self.assertTrue((target / "lib" / "libfoo.a").exists())
        self.assertTrue((target / "include" / "foo.h").exists())

    def test_exports_nested_includes(self) -> None:
        """install_artifacts preserves nested include directory structure."""
        repo_root = paths.repo_root()
        script = ZScript()

        output_src = paths.out_dir()
        (output_src / "include" / "sub").mkdir(parents=True)
        (output_src / "include" / "sub" / "bar.h").write_text("// bar")

        target = repo_root / "export"
        script.install_artifacts(str(target))

        self.assertTrue((target / "include" / "sub" / "bar.h").exists())

    def test_no_output_dir_produces_empty_export(self) -> None:
        """install_artifacts with no .nanvix/out/ produces empty target."""
        repo_root = paths.repo_root()
        script = ZScript()

        target = repo_root / "export"
        script.install_artifacts(str(target))

        self.assertTrue(target.is_dir())
        # No lib/ or include/ created when output/ doesn't exist
        self.assertFalse((target / "lib").exists())
        self.assertFalse((target / "include").exists())

    def test_creates_output_directory(self) -> None:
        """install_artifacts creates the target directory if missing."""
        repo_root = paths.repo_root()
        script = ZScript()

        target = repo_root / "nonexistent" / "path"
        script.install_artifacts(str(target))

        self.assertTrue(target.is_dir())


if __name__ == "__main__":
    unittest.main()
