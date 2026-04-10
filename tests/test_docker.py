# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.docker."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from nanvix_zutil.docker import (
    BUILDROOT_CONTAINER_PATH,
    DEFAULT_DOCKER_IMAGE,
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    is_windows,
    docker_available,
    image_exists,
)


class TestMount(unittest.TestCase):
    """Tests for the Mount dataclass."""

    def test_default_not_readonly(self) -> None:
        m = Mount(
            host_path=Path("/host/path"),
            container_path=PurePosixPath("/container/path"),
        )
        self.assertFalse(m.readonly)

    def test_readonly_flag(self) -> None:
        m = Mount(
            host_path=Path("/host/path"),
            container_path=PurePosixPath("/container/path"),
            readonly=True,
        )
        self.assertTrue(m.readonly)

    def test_fields_stored(self) -> None:
        m = Mount(
            host_path=Path("/a"), container_path=PurePosixPath("/b"), readonly=True
        )
        self.assertEqual(m.host_path, Path("/a"))
        self.assertEqual(m.container_path, PurePosixPath("/b"))


class TestDockerConfigTranslatePath(unittest.TestCase):
    """Tests for DockerConfig.translate_path."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._workspace = Path(self._tmpdir.name) / "workspace"
        self._workspace.mkdir(parents=True)
        self._sysroot = Path(self._tmpdir.name) / "sysroot"
        self._sysroot.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_config(self) -> DockerConfig:
        return DockerConfig(
            image="test-image",
            mounts=[
                Mount(
                    host_path=self._workspace,
                    container_path=WORKSPACE_CONTAINER_PATH,
                ),
                Mount(
                    host_path=self._sysroot,
                    container_path=SYSROOT_CONTAINER_PATH,
                    readonly=True,
                ),
            ],
        )

    def test_workspace_root(self) -> None:
        cfg = self._make_config()
        result = cfg.translate_path(self._workspace)
        self.assertEqual(result, WORKSPACE_CONTAINER_PATH)

    def test_workspace_child(self) -> None:
        cfg = self._make_config()
        result = cfg.translate_path(self._workspace / "src" / "main.c")
        self.assertEqual(result, WORKSPACE_CONTAINER_PATH / "src" / "main.c")

    def test_sysroot_root(self) -> None:
        cfg = self._make_config()
        result = cfg.translate_path(self._sysroot)
        self.assertEqual(result, SYSROOT_CONTAINER_PATH)

    def test_sysroot_child(self) -> None:
        cfg = self._make_config()
        result = cfg.translate_path(self._sysroot / "lib" / "libposix.a")
        self.assertEqual(result, SYSROOT_CONTAINER_PATH / "lib" / "libposix.a")

    def test_unmatched_path_returned_unchanged(self) -> None:
        cfg = self._make_config()
        unmatched = Path("/some/other/path")
        result = cfg.translate_path(unmatched)
        self.assertEqual(result, unmatched)

    def test_empty_mounts_returns_input(self) -> None:
        cfg = DockerConfig(image="test-image", mounts=[])
        p = Path("/foo/bar")
        self.assertEqual(cfg.translate_path(p), p)


class TestDockerConfigBuildRunCmd(unittest.TestCase):
    """Tests for DockerConfig.build_run_cmd."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._workspace = Path(self._tmpdir.name) / "workspace"
        self._workspace.mkdir(parents=True)
        self._sysroot = Path(self._tmpdir.name) / "sysroot"
        self._sysroot.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_config(self) -> DockerConfig:
        return DockerConfig(
            image="nanvix/toolchain:latest-minimal",
            mounts=[
                Mount(
                    host_path=self._workspace,
                    container_path=WORKSPACE_CONTAINER_PATH,
                    readonly=False,
                ),
                Mount(
                    host_path=self._sysroot,
                    container_path=SYSROOT_CONTAINER_PATH,
                    readonly=True,
                ),
            ],
            uid=1000,
            gid=1000,
        )

    def test_starts_with_docker_run(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("make", "all")
        self.assertEqual(cmd[:3], ["docker", "run", "--rm"])

    def test_contains_image(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("make", "all")
        self.assertIn("nanvix/toolchain:latest-minimal", cmd)

    def test_contains_user(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        self.assertIn("--user", cmd)
        self.assertIn("1000:1000", cmd)

    def test_workspace_mount(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        workspace_vol = f"{self._workspace.resolve()}:{WORKSPACE_CONTAINER_PATH}"
        self.assertIn(workspace_vol, cmd)

    def test_sysroot_mount_readonly(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        sysroot_vol = f"{self._sysroot.resolve()}:{SYSROOT_CONTAINER_PATH}:ro"
        self.assertIn(sysroot_vol, cmd)

    def test_workdir_set(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        self.assertIn("-w", cmd)
        w_idx = cmd.index("-w")
        self.assertEqual(cmd[w_idx + 1], str(WORKSPACE_CONTAINER_PATH))

    def test_home_env(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        self.assertIn("HOME=/tmp", cmd)

    def test_inner_command_appended(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("make", "-j4", "all")
        self.assertTrue(cmd[-3:] == ["make", "-j4", "all"])

    def test_extra_env_forwarded(self) -> None:
        cfg = self._make_config()
        cfg.extra_env["MY_VAR"] = "hello"
        cmd = cfg.build_run_cmd("echo")
        self.assertIn("MY_VAR=hello", cmd)


class TestDockerConfigBuildKvmRunCmd(unittest.TestCase):
    """Tests for DockerConfig.build_kvm_run_cmd."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._workspace = Path(self._tmpdir.name) / "workspace"
        self._workspace.mkdir(parents=True)
        self._sysroot = Path(self._tmpdir.name) / "sysroot"
        self._sysroot.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_config(self) -> DockerConfig:
        return DockerConfig(
            image="nanvix/toolchain:latest-minimal",
            mounts=[
                Mount(
                    host_path=self._workspace,
                    container_path=WORKSPACE_CONTAINER_PATH,
                ),
                Mount(
                    host_path=self._sysroot,
                    container_path=SYSROOT_CONTAINER_PATH,
                    readonly=True,
                ),
            ],
            uid=1000,
            gid=1000,
        )

    def test_contains_kvm_device(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_kvm_run_cmd(
            "./bin/nanvixd.elf", "--", "/mnt/workspace/hello.elf"
        )
        self.assertIn("--device", cmd)
        self.assertIn("/dev/kvm", cmd)

    def test_workdir_defaults_to_workspace(self) -> None:
        """build_kvm_run_cmd uses self.workdir (WORKSPACE by default)."""
        cfg = self._make_config()
        cmd = cfg.build_kvm_run_cmd("echo")
        self.assertIn("-w", cmd)
        w_idx = cmd.index("-w")
        self.assertEqual(cmd[w_idx + 1], str(WORKSPACE_CONTAINER_PATH))

    def test_workdir_respects_custom_override(self) -> None:
        """build_kvm_run_cmd honours a custom workdir override."""
        cfg = self._make_config()
        cfg.workdir = SYSROOT_CONTAINER_PATH
        cmd = cfg.build_kvm_run_cmd("echo")
        self.assertIn("-w", cmd)
        w_idx = cmd.index("-w")
        self.assertEqual(cmd[w_idx + 1], str(SYSROOT_CONTAINER_PATH))

    def test_sysroot_mount_writable(self) -> None:
        """Sysroot must NOT have :ro suffix in KVM mode."""
        cfg = self._make_config()
        cmd = cfg.build_kvm_run_cmd("echo")
        sysroot_ro = f"{self._sysroot.resolve()}:{SYSROOT_CONTAINER_PATH}:ro"
        self.assertNotIn(sysroot_ro, cmd)

    def test_non_sysroot_readonly_mount_preserved(self) -> None:
        """Non-sysroot readonly mounts keep :ro in KVM mode."""
        buildroot = self._workspace / "buildroot"
        buildroot.mkdir()
        cfg = DockerConfig(
            image="test-image",
            mounts=[
                Mount(
                    host_path=self._workspace,
                    container_path=WORKSPACE_CONTAINER_PATH,
                    readonly=False,
                ),
                Mount(
                    host_path=self._sysroot,
                    container_path=SYSROOT_CONTAINER_PATH,
                    readonly=True,
                ),
                Mount(
                    host_path=buildroot,
                    container_path=BUILDROOT_CONTAINER_PATH,
                    readonly=True,
                ),
            ],
            uid=1000,
            gid=1000,
        )
        cmd = cfg.build_kvm_run_cmd("echo")
        cmd_str = " ".join(cmd)
        # Buildroot readonly mount should have :ro
        self.assertIn(f"{buildroot.resolve()}:{BUILDROOT_CONTAINER_PATH}:ro", cmd_str)
        # Sysroot readonly mount should NOT have :ro (forced writable)
        self.assertNotIn(
            f"{self._sysroot.resolve()}:{SYSROOT_CONTAINER_PATH}:ro", cmd_str
        )
        # Workspace (non-readonly) should not have :ro
        self.assertNotIn(
            f"{self._workspace.resolve()}:{WORKSPACE_CONTAINER_PATH}:ro", cmd_str
        )

    def test_user_env_set(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_kvm_run_cmd("echo")
        user_entries = [e for e in cmd if e.startswith("USER=")]
        self.assertTrue(user_entries, "USER env var should be present in KVM run")

    def test_user_env_falls_back_to_username(self) -> None:
        """On Windows, USER is absent; USERNAME should be used."""
        cfg = self._make_config()
        env = os.environ.copy()
        env.pop("USER", None)
        env["USERNAME"] = "winuser"
        with patch.dict(os.environ, env, clear=True):
            cmd = cfg.build_kvm_run_cmd("echo")
        user_entries = [e for e in cmd if e.startswith("USER=")]
        self.assertTrue(any("winuser" in e for e in user_entries))

    def test_user_env_defaults_to_nanvix(self) -> None:
        """When neither USER nor USERNAME is set, falls back to 'nanvix'."""
        cfg = self._make_config()
        env = os.environ.copy()
        env.pop("USER", None)
        env.pop("USERNAME", None)
        with patch.dict(os.environ, env, clear=True):
            cmd = cfg.build_kvm_run_cmd("echo")
        user_entries = [e for e in cmd if e.startswith("USER=")]
        self.assertTrue(any("nanvix" in e for e in user_entries))

    def test_inner_command_appended(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_kvm_run_cmd("./bin/nanvixd.elf", "--", "hello.elf")
        self.assertTrue(cmd[-3:] == ["./bin/nanvixd.elf", "--", "hello.elf"])


class TestDockerAvailable(unittest.TestCase):
    """Tests for docker_available()."""

    def test_returns_bool(self) -> None:
        result = docker_available()
        self.assertIsInstance(result, bool)

    def test_false_when_docker_not_on_path(self) -> None:
        with patch("nanvix_zutil.docker.shutil.which", return_value=None):
            self.assertFalse(docker_available())

    def test_true_when_docker_on_path(self) -> None:
        with patch("nanvix_zutil.docker.shutil.which", return_value="/usr/bin/docker"):
            self.assertTrue(docker_available())


class TestImageExists(unittest.TestCase):
    """Tests for image_exists()."""

    def test_false_when_docker_not_available(self) -> None:
        with patch("nanvix_zutil.docker.docker_available", return_value=False):
            self.assertFalse(image_exists("any-image"))

    def test_false_when_inspect_fails(self) -> None:
        fake_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=1
        )
        with (
            patch("nanvix_zutil.docker.docker_available", return_value=True),
            patch("nanvix_zutil.docker.subprocess.run", return_value=fake_result),
        ):
            self.assertFalse(image_exists("nonexistent-image"))

    def test_true_when_inspect_succeeds(self) -> None:
        fake_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        with (
            patch("nanvix_zutil.docker.docker_available", return_value=True),
            patch("nanvix_zutil.docker.subprocess.run", return_value=fake_result),
        ):
            self.assertTrue(image_exists("nanvix/toolchain:latest-minimal"))


class TestKvmGidInKvmRunCmd(unittest.TestCase):
    """Tests for KVM GID behaviour in build_kvm_run_cmd."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._workspace = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_config(self) -> DockerConfig:
        return DockerConfig(
            image="test-image",
            mounts=[
                Mount(
                    host_path=self._workspace,
                    container_path=WORKSPACE_CONTAINER_PATH,
                ),
            ],
        )

    def test_group_add_absent_when_no_kvm(self) -> None:
        """When /dev/kvm is inaccessible, --group-add is omitted."""
        cfg = self._make_config()
        with patch("nanvix_zutil.docker.os.stat", side_effect=OSError):
            cmd = cfg.build_kvm_run_cmd("echo")
        self.assertNotIn("--group-add", cmd)

    def test_group_add_present_when_kvm_accessible(self) -> None:
        """When /dev/kvm has a GID, --group-add <gid> is included."""
        cfg = self._make_config()
        mock_stat = type("FakeStat", (), {"st_gid": 42})()
        with patch("nanvix_zutil.docker.os.stat", return_value=mock_stat):
            cmd = cfg.build_kvm_run_cmd("echo")
        self.assertIn("--group-add", cmd)
        gid_idx = cmd.index("--group-add")
        self.assertEqual(cmd[gid_idx + 1], "42")


class TestKvmPlatformWarning(unittest.TestCase):
    """Tests for KVM platform warning in build_kvm_run_cmd."""

    def test_warns_on_non_linux(self) -> None:
        """build_kvm_run_cmd warns when not running on Linux."""
        cfg = DockerConfig(image="test-image", mounts=[], uid=0, gid=0)
        with patch("nanvix_zutil.docker.sys") as mock_sys:
            mock_sys.platform = "win32"
            with self.assertWarns(UserWarning) as ctx:
                cfg.build_kvm_run_cmd("echo")
            self.assertIn("KVM is only available on Linux", str(ctx.warning))

    def test_no_warning_on_linux(self) -> None:
        """build_kvm_run_cmd does not warn on Linux."""
        import warnings as _warnings

        cfg = DockerConfig(image="test-image", mounts=[], uid=0, gid=0)
        with patch("nanvix_zutil.docker.sys") as mock_sys:
            mock_sys.platform = "linux"
            with _warnings.catch_warnings(record=True) as w:
                _warnings.simplefilter("always")
                cfg.build_kvm_run_cmd("echo")
            kvm_warnings = [x for x in w if "KVM" in str(x.message)]
            self.assertEqual(len(kvm_warnings), 0)


class TestWellKnownPaths(unittest.TestCase):
    """Verify the well-known container path constants."""

    def test_workspace_path(self) -> None:
        self.assertEqual(WORKSPACE_CONTAINER_PATH, PurePosixPath("/mnt/workspace"))

    def test_sysroot_path(self) -> None:
        self.assertEqual(SYSROOT_CONTAINER_PATH, PurePosixPath("/mnt/sysroot"))

    def test_buildroot_path(self) -> None:
        self.assertEqual(BUILDROOT_CONTAINER_PATH, PurePosixPath("/mnt/buildroot"))

    def test_toolchain_path(self) -> None:
        self.assertEqual(TOOLCHAIN_CONTAINER_PATH, PurePosixPath("/opt/nanvix"))

    def test_default_image(self) -> None:
        self.assertEqual(DEFAULT_DOCKER_IMAGE, "nanvix/toolchain:latest-minimal")


class TestPlatformUidGid(unittest.TestCase):
    """Tests for platform-aware UID/GID helpers."""

    def test_get_uid_returns_int(self) -> None:
        """_get_uid() always returns an int matching os.getuid on Linux."""
        from nanvix_zutil.docker import _get_uid  # pyright: ignore[reportPrivateUsage]

        result = _get_uid()
        self.assertIsInstance(result, int)
        if hasattr(os, "getuid"):
            self.assertEqual(result, os.getuid())

    def test_get_gid_returns_int(self) -> None:
        """_get_gid() always returns an int matching os.getgid on Linux."""
        from nanvix_zutil.docker import _get_gid  # pyright: ignore[reportPrivateUsage]

        result = _get_gid()
        self.assertIsInstance(result, int)
        if hasattr(os, "getgid"):
            self.assertEqual(result, os.getgid())

    def test_get_uid_fallback_when_no_getuid(self) -> None:
        """_get_uid() returns 0 when os.getuid is absent (Windows simulation)."""
        from nanvix_zutil.docker import _get_uid  # pyright: ignore[reportPrivateUsage]

        saved = getattr(os, "getuid", None)
        try:
            if saved is not None:
                delattr(os, "getuid")
            self.assertEqual(_get_uid(), 0)
        finally:
            if saved is not None:
                os.getuid = saved  # type: ignore[attr-defined]

    def test_get_gid_fallback_when_no_getgid(self) -> None:
        """_get_gid() returns 0 when os.getgid is absent (Windows simulation)."""
        from nanvix_zutil.docker import _get_gid  # pyright: ignore[reportPrivateUsage]

        saved = getattr(os, "getgid", None)
        try:
            if saved is not None:
                delattr(os, "getgid")
            self.assertEqual(_get_gid(), 0)
        finally:
            if saved is not None:
                os.getgid = saved  # type: ignore[attr-defined]

    def test_docker_config_default_uid_gid(self) -> None:
        """DockerConfig without explicit uid/gid uses _get_uid/_get_gid."""
        cfg = DockerConfig(image="test-image")
        if hasattr(os, "getuid"):
            self.assertEqual(cfg.uid, os.getuid())
        if hasattr(os, "getgid"):
            self.assertEqual(cfg.gid, os.getgid())


class TestIsWindows(unittest.TestCase):
    """Tests for is_windows() helper."""

    def test_returns_bool(self) -> None:
        self.assertIsInstance(is_windows(), bool)

    def test_false_on_linux(self) -> None:
        with patch("nanvix_zutil.docker.sys") as mock_sys:
            mock_sys.platform = "linux"
            self.assertFalse(is_windows())

    def test_true_on_win32(self) -> None:
        with patch("nanvix_zutil.docker.sys") as mock_sys:
            mock_sys.platform = "win32"
            self.assertTrue(is_windows())


class TestDockerConfigWindowsFields(unittest.TestCase):
    """DockerConfig Windows-specific field defaults."""

    def test_crlf_files_default_empty(self) -> None:
        cfg = DockerConfig(image="test-image")
        self.assertEqual(cfg.crlf_files, [])

    def test_output_files_default_empty(self) -> None:
        cfg = DockerConfig(image="test-image")
        self.assertEqual(cfg.output_files, [])

    def test_tar_excludes_has_defaults(self) -> None:
        cfg = DockerConfig(image="test-image")
        self.assertIn(".git", cfg.tar_excludes)
        self.assertIn(".nanvix/venv", cfg.tar_excludes)

    def test_container_build_dir_default(self) -> None:
        cfg = DockerConfig(image="test-image")
        self.assertEqual(cfg.container_build_dir, "/tmp/build")


class TestDockerConfigBuildWindowsRunCmd(unittest.TestCase):
    """Tests for DockerConfig.build_windows_run_cmd()."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._workspace = Path(self._tmpdir.name) / "workspace"
        self._workspace.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_config(
        self,
        crlf_files: list[str] | None = None,
        output_files: list[str] | None = None,
    ) -> DockerConfig:
        return DockerConfig(
            image="nanvix/toolchain:latest-minimal",
            mounts=[
                Mount(
                    host_path=self._workspace,
                    container_path=WORKSPACE_CONTAINER_PATH,
                    readonly=False,
                ),
            ],
            uid=1000,
            gid=1000,
            crlf_files=crlf_files or [],
            output_files=output_files or [],
        )

    def test_tar_copy_command_structure(self) -> None:
        """Windows run command uses tar instead of bind mounts."""
        cfg = self._make_config()
        cmd = cfg.build_windows_run_cmd("make", "all")
        self.assertEqual(cmd[:3], ["docker", "run", "--rm"])
        # Should use sh -c wrapping.
        self.assertIn("sh", cmd)
        self.assertIn("-c", cmd)

    def test_contains_tar_in_shell_script(self) -> None:
        """The shell script should include tar commands."""
        cfg = self._make_config()
        cmd = cfg.build_windows_run_cmd("make", "all")
        shell_script = cmd[-1]  # Last arg after sh -c
        self.assertIn("tar -cf", shell_script)
        self.assertIn("tar -xf", shell_script)

    def test_crlf_normalization_included(self) -> None:
        """CRLF files are normalized in the shell script."""
        cfg = self._make_config(crlf_files=["Makefile", "configure"])
        cmd = cfg.build_windows_run_cmd("make", "all")
        shell_script = cmd[-1]
        self.assertIn("Makefile", shell_script)
        self.assertIn("configure", shell_script)
        self.assertIn("sed", shell_script)

    def test_output_files_copied_back(self) -> None:
        """Output files are copied from container to host."""
        cfg = self._make_config(output_files=["build/output.elf", "result.bin"])
        cmd = cfg.build_windows_run_cmd("make", "all")
        shell_script = cmd[-1]
        self.assertIn("build/output.elf", shell_script)
        self.assertIn("result.bin", shell_script)
        self.assertIn("cp -f", shell_script)
        self.assertIn("mkdir -p", shell_script)

    def test_inner_command_in_script(self) -> None:
        """The inner command appears in the shell script."""
        cfg = self._make_config()
        cmd = cfg.build_windows_run_cmd("make", "-j4", "all")
        shell_script = cmd[-1]
        self.assertIn("make -j4 all", shell_script)

    def test_workdir_is_build_dir(self) -> None:
        """Working directory is set to container_build_dir."""
        cfg = self._make_config()
        cmd = cfg.build_windows_run_cmd("echo")
        w_idx = cmd.index("-w")
        self.assertEqual(cmd[w_idx + 1], "/tmp/build")

    def test_tar_excludes_in_command(self) -> None:
        """Tar excludes appear in the shell script."""
        cfg = self._make_config()
        cmd = cfg.build_windows_run_cmd("echo")
        shell_script = cmd[-1]
        self.assertIn("--exclude=.git", shell_script)

    def test_no_user_flag(self) -> None:
        """Windows run cmd does not include --user flag."""
        cfg = self._make_config()
        cmd = cfg.build_windows_run_cmd("echo")
        self.assertNotIn("--user", cmd)

    def test_extra_env_forwarded(self) -> None:
        """Extra env vars are forwarded to the container."""
        cfg = self._make_config()
        cfg.extra_env["MY_VAR"] = "hello"
        cmd = cfg.build_windows_run_cmd("echo")
        self.assertIn("MY_VAR=hello", cmd)


class TestTranslateWindowsPath(unittest.TestCase):
    """Tests for _translate_windows_path() helper."""

    def test_windows_c_drive(self) -> None:
        """C:\\Users\\foo\\repo → /c/Users/foo/repo."""
        from nanvix_zutil.docker import (
            _translate_windows_path,  # pyright: ignore[reportPrivateUsage]
        )

        result = _translate_windows_path(Path("C:\\Users\\foo\\repo"))
        self.assertEqual(result, "/c/Users/foo/repo")

    def test_windows_d_drive(self) -> None:
        """D:\\builds → /d/builds."""
        from nanvix_zutil.docker import (
            _translate_windows_path,  # pyright: ignore[reportPrivateUsage]
        )

        result = _translate_windows_path(Path("D:\\builds"))
        self.assertEqual(result, "/d/builds")

    def test_posix_path_unchanged(self) -> None:
        """/home/user/repo → /home/user/repo (no-op on POSIX)."""
        from nanvix_zutil.docker import (
            _translate_windows_path,  # pyright: ignore[reportPrivateUsage]
        )

        result = _translate_windows_path(Path("/home/user/repo"))
        self.assertEqual(result, "/home/user/repo")

    def test_mixed_separators(self) -> None:
        """C:/Users\\foo → /c/Users/foo."""
        from nanvix_zutil.docker import (
            _translate_windows_path,  # pyright: ignore[reportPrivateUsage]
        )

        result = _translate_windows_path(Path("C:/Users\\foo"))
        # On Linux, Path("C:/Users\\foo") preserves forward slashes.
        # The function should still normalise backslashes.
        self.assertTrue(result.startswith("/c/") or result == "C:/Users/foo")

    def test_short_path_no_crash(self) -> None:
        """Very short paths should not crash the function."""
        from nanvix_zutil.docker import (
            _translate_windows_path,  # pyright: ignore[reportPrivateUsage]
        )

        result = _translate_windows_path(Path("ab"))
        self.assertIsInstance(result, str)


class TestGetKvmGidPlatformShortCircuit(unittest.TestCase):
    """Tests for _get_kvm_gid() non-Linux short-circuit."""

    def test_returns_empty_on_non_linux(self) -> None:
        """_get_kvm_gid() returns '' immediately on non-Linux."""
        from nanvix_zutil.docker import (
            _get_kvm_gid,  # pyright: ignore[reportPrivateUsage]
        )

        with patch("nanvix_zutil.docker.sys") as mock_sys:
            mock_sys.platform = "win32"
            result = _get_kvm_gid()
        self.assertEqual(result, "")

    def test_returns_gid_on_linux(self) -> None:
        """_get_kvm_gid() reads /dev/kvm on Linux."""
        from nanvix_zutil.docker import (
            _get_kvm_gid,  # pyright: ignore[reportPrivateUsage]
        )

        mock_stat = type("FakeStat", (), {"st_gid": 108})()
        with (
            patch("nanvix_zutil.docker.sys") as mock_sys,
            patch("nanvix_zutil.docker.os.stat", return_value=mock_stat),
        ):
            mock_sys.platform = "linux"
            result = _get_kvm_gid()
        self.assertEqual(result, "108")


if __name__ == "__main__":
    unittest.main()
