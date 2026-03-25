# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.docker."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nanvix_zutil.docker import (
    DEFAULT_DOCKER_IMAGE,
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    docker_available,
    image_exists,
)


class TestMount(unittest.TestCase):
    """Tests for the Mount dataclass."""

    def test_default_not_readonly(self) -> None:
        m = Mount(host_path=Path("/host/path"), container_path=Path("/container/path"))
        self.assertFalse(m.readonly)

    def test_readonly_flag(self) -> None:
        m = Mount(
            host_path=Path("/host/path"),
            container_path=Path("/container/path"),
            readonly=True,
        )
        self.assertTrue(m.readonly)

    def test_fields_stored(self) -> None:
        m = Mount(host_path=Path("/a"), container_path=Path("/b"), readonly=True)
        self.assertEqual(m.host_path, Path("/a"))
        self.assertEqual(m.container_path, Path("/b"))


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

    def test_workdir_is_sysroot(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_kvm_run_cmd("echo")
        w_idx = cmd.index("-w")
        self.assertEqual(cmd[w_idx + 1], str(SYSROOT_CONTAINER_PATH))

    def test_sysroot_mount_writable(self) -> None:
        """Sysroot must NOT have :ro suffix in KVM mode."""
        cfg = self._make_config()
        cmd = cfg.build_kvm_run_cmd("echo")
        sysroot_ro = f"{self._sysroot.resolve()}:{SYSROOT_CONTAINER_PATH}:ro"
        self.assertNotIn(sysroot_ro, cmd)

    def test_user_env_set(self) -> None:
        cfg = self._make_config()
        cmd = cfg.build_kvm_run_cmd("echo")
        user_entries = [e for e in cmd if e.startswith("USER=")]
        self.assertTrue(user_entries, "USER env var should be present in KVM run")

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


class TestWellKnownPaths(unittest.TestCase):
    """Verify the well-known container path constants."""

    def test_workspace_path(self) -> None:
        self.assertEqual(WORKSPACE_CONTAINER_PATH, Path("/mnt/workspace"))

    def test_sysroot_path(self) -> None:
        self.assertEqual(SYSROOT_CONTAINER_PATH, Path("/mnt/sysroot"))

    def test_toolchain_path(self) -> None:
        self.assertEqual(TOOLCHAIN_CONTAINER_PATH, Path("/opt/nanvix"))

    def test_default_image(self) -> None:
        self.assertEqual(DEFAULT_DOCKER_IMAGE, "nanvix/toolchain:latest-minimal")


if __name__ == "__main__":
    unittest.main()
