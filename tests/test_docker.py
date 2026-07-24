# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.docker."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from nanvix_zutil.docker import (
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    is_windows,
    remove_build_volume,
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

    def test_unmatched_path_returned_as_posix(self) -> None:
        cfg = self._make_config()
        unmatched = Path("/some/other/path")
        result = cfg.translate_path(unmatched)
        self.assertEqual(result, PurePosixPath("/some/other/path"))
        self.assertIsInstance(result, PurePosixPath)

    def test_empty_mounts_returns_posix(self) -> None:
        cfg = DockerConfig(image="test-image", mounts=[])
        p = Path("/foo/bar")
        result = cfg.translate_path(p)
        self.assertEqual(result, PurePosixPath("/foo/bar"))
        self.assertIsInstance(result, PurePosixPath)


@patch("nanvix_zutil.docker.is_windows", return_value=False)
class TestDockerConfigBuildRunCmd(unittest.TestCase):
    """Tests for DockerConfig.build_run_cmd.

    ``is_windows`` is patched to ``False`` so volume-string assertions
    use raw host paths (no drive-letter translation).  The Windows
    equivalent (``build_windows_run_cmd``) is tested separately.
    """

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
            image="ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f",
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

    def test_starts_with_docker_run(self, _mock: object) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("make", "all")
        self.assertEqual(cmd[:3], ["docker", "run", "--rm"])

    def test_contains_image(self, _mock: object) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("make", "all")
        self.assertIn(
            "ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f",
            cmd,
        )

    def test_contains_user(self, _mock: object) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        self.assertIn("--user", cmd)
        self.assertIn("1000:1000", cmd)

    def test_workspace_mount(self, _mock: object) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        workspace_vol = f"{self._workspace.resolve()}:{WORKSPACE_CONTAINER_PATH}"
        self.assertIn(workspace_vol, cmd)

    def test_sysroot_mount_readonly(self, _mock: object) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        sysroot_vol = f"{self._sysroot.resolve()}:{SYSROOT_CONTAINER_PATH}:ro"
        self.assertIn(sysroot_vol, cmd)

    def test_workdir_set(self, _mock: object) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        self.assertIn("-w", cmd)
        w_idx = cmd.index("-w")
        self.assertEqual(cmd[w_idx + 1], str(WORKSPACE_CONTAINER_PATH))

    def test_home_env(self, _mock: object) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("echo")
        self.assertIn("HOME=/tmp", cmd)

    def test_inner_command_appended(self, _mock: object) -> None:
        cfg = self._make_config()
        cmd = cfg.build_run_cmd("make", "-j4", "all")
        self.assertTrue(cmd[-3:] == ["make", "-j4", "all"])

    def test_extra_env_forwarded(self, _mock: object) -> None:
        cfg = self._make_config()
        cfg.extra_env["MY_VAR"] = "hello"
        cmd = cfg.build_run_cmd("echo")
        self.assertIn("MY_VAR=hello", cmd)


class TestWellKnownPaths(unittest.TestCase):
    """Verify the well-known container path constants."""

    def test_workspace_path(self) -> None:
        self.assertEqual(WORKSPACE_CONTAINER_PATH, PurePosixPath("/mnt/workspace"))

    def test_sysroot_path(self) -> None:
        self.assertEqual(SYSROOT_CONTAINER_PATH, PurePosixPath("/mnt/sysroot"))

    def test_toolchain_path(self) -> None:
        self.assertEqual(TOOLCHAIN_CONTAINER_PATH, PurePosixPath("/opt/nanvix"))


class TestPlatformUidGid(unittest.TestCase):
    """Tests for platform-aware UID/GID helpers."""

    def test_get_uid_returns_int(self) -> None:
        """_get_uid() always returns an int matching os.getuid on Linux."""
        from nanvix_zutil.docker import _get_uid  # pyright: ignore[reportPrivateUsage]

        result = _get_uid()
        self.assertIsInstance(result, int)
        if hasattr(os, "getuid"):
            self.assertEqual(
                result,
                os.getuid(),  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
            )

    def test_get_gid_returns_int(self) -> None:
        """_get_gid() always returns an int matching os.getgid on Linux."""
        from nanvix_zutil.docker import _get_gid  # pyright: ignore[reportPrivateUsage]

        result = _get_gid()
        self.assertIsInstance(result, int)
        if hasattr(os, "getgid"):
            self.assertEqual(
                result,
                os.getgid(),  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
            )

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
                os.getuid = saved  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]

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
                os.getgid = saved  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]

    def test_docker_config_default_uid_gid(self) -> None:
        """DockerConfig without explicit uid/gid uses _get_uid/_get_gid."""
        cfg = DockerConfig(image="test-image")
        if hasattr(os, "getuid"):
            self.assertEqual(
                cfg.uid,
                os.getuid(),  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
            )
        if hasattr(os, "getgid"):
            self.assertEqual(
                cfg.gid,
                os.getgid(),  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
            )


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
        output_files: list[str] | None = None,
        crlf_files: list[str] | None = None,
        persistent_volume: bool | str = False,
    ) -> DockerConfig:
        return DockerConfig(
            image="ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f",
            mounts=[
                Mount(
                    host_path=self._workspace,
                    container_path=WORKSPACE_CONTAINER_PATH,
                    readonly=False,
                ),
            ],
            uid=1000,
            gid=1000,
            output_files=output_files or [],
            crlf_files=crlf_files or [],
            persistent_volume=persistent_volume,
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
        """The shell script should include tar commands (rsync fallback)."""
        cfg = self._make_config()
        cmd = cfg.build_windows_run_cmd("make", "all")
        shell_script = cmd[-1]  # Last arg after sh -c
        self.assertIn("tar -cf", shell_script)
        self.assertIn("tar -xf", shell_script)

    def test_prefers_rsync_with_tar_fallback(self) -> None:
        """Sync prefers rsync and falls back to tar."""
        cfg = self._make_config()
        cmd = cfg.build_windows_run_cmd("make", "all")
        shell_script = cmd[-1]
        self.assertIn("command -v rsync", shell_script)
        self.assertIn("rsync -a --exclude", shell_script)
        self.assertNotIn("--delete", shell_script)
        self.assertIn("else tar -cf", shell_script)

    def test_no_crlf_normalization_by_default(self) -> None:
        """Without crlf_files, no normalization is emitted."""
        cfg = self._make_config()
        shell_script = cfg.build_windows_run_cmd("make")[-1]
        self.assertNotIn("tr -d", shell_script)

    def test_crlf_normalization_guarded_per_file(self) -> None:
        """Each crlf file gets a guarded, portable normalization after sync."""
        cfg = self._make_config(crlf_files=["configure", "Makefile.in"])
        shell_script = cfg.build_windows_run_cmd("make")[-1]
        self.assertIn("if [ -f", shell_script)
        self.assertIn(r"tr -d '\r'", shell_script)
        # Written back with cat (not mv) to preserve the file's mode.
        self.assertIn(
            "cat /tmp/build/configure.crlf.tmp > /tmp/build/configure", shell_script
        )
        self.assertIn("/tmp/build/Makefile.in", shell_script)
        # Normalization runs after cd into the build dir, before the command.
        self.assertLess(
            shell_script.index("cd /tmp/build"), shell_script.index("tr -d")
        )
        self.assertLess(shell_script.index("tr -d"), shell_script.index("make"))

    def test_file_outputs_copied_back(self) -> None:
        """File outputs are copied to the mirrored path on the host."""
        cfg = self._make_config(output_files=["build/output.elf", "result.bin"])
        cmd = cfg.build_windows_run_cmd("make", "all")
        shell_script = cmd[-1]
        self.assertIn("/tmp/build/build/output.elf", shell_script)
        self.assertIn("/mnt/workspace/build/output.elf", shell_script)
        self.assertIn("/mnt/workspace/result.bin", shell_script)
        self.assertIn("cp -f", shell_script)
        self.assertIn("mkdir -p", shell_script)

    def test_no_output_files_by_default(self) -> None:
        """Without output_files, no copy-back is emitted."""
        shell_script = self._make_config().build_windows_run_cmd("make")[-1]
        self.assertNotIn("rm -rf", shell_script)
        self.assertNotIn("cp -f", shell_script)

    @patch("nanvix_zutil.docker.is_windows", return_value=False)
    def test_output_dir_copied_back_with_wipe(self, _mock: object) -> None:
        """A directory output is wiped then copied back with cp -a (Linux host)."""
        cfg = self._make_config(output_files=["_install_staging"])
        shell_script = cfg.build_windows_run_cmd("make")[-1]
        self.assertIn("if [ -d /tmp/build/_install_staging ]", shell_script)
        self.assertIn("rm -rf /mnt/workspace/_install_staging", shell_script)
        self.assertIn("mkdir -p /mnt/workspace/_install_staging", shell_script)
        self.assertIn(
            "cp -a /tmp/build/_install_staging/. /mnt/workspace/_install_staging/",
            shell_script,
        )
        # Same entry also handles the file case via elif.
        self.assertIn("elif [ -f /tmp/build/_install_staging ]", shell_script)
        # Copied-back outputs are chown'd to host uid:gid.
        self.assertIn(
            "chown -R 1000:1000 /mnt/workspace/_install_staging", shell_script
        )

    @patch("nanvix_zutil.docker.is_windows", return_value=True)
    def test_no_chown_on_windows_host(self, _mock: object) -> None:
        """On a Windows host, copy-back does not chown (would force root:root)."""
        cfg = self._make_config(output_files=["a.elf", ".nanvix/out/x"])
        shell_script = cfg.build_windows_run_cmd("make")[-1]
        self.assertNotIn("chown", shell_script)
        # Symlinks are dereferenced (cp -aL) so no Windows reparse points.
        self.assertIn(
            "cp -aL /tmp/build/.nanvix/out/x/. /mnt/workspace/.nanvix/out/x/",
            shell_script,
        )
        # Still valid shell without the chowns.
        if shutil.which("sh"):
            proc = subprocess.run(["sh", "-n", "-c", shell_script], capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    @unittest.skipUnless(shutil.which("sh"), "POSIX sh not on PATH")
    def test_generated_script_is_valid_shell(self) -> None:
        """The full generated script parses under POSIX sh (guards `;;` etc.)."""
        cfg = self._make_config(
            crlf_files=["configure"],
            output_files=["a.elf", ".nanvix/out/staging/x"],
        )
        script = cfg.build_windows_run_cmd("sh", "-c", "make build")[-1]
        proc = subprocess.run(["sh", "-n", "-c", script], capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    def test_invalid_output_path_rejected(self) -> None:
        """Absolute paths and paths at/above the workspace root are rejected."""
        for bad in ("", ".", "..", "../escape", "/out", "/"):
            with self.assertRaises(ValueError):
                self._make_config(output_files=[bad]).build_windows_run_cmd("make")

    def test_no_named_volume_by_default(self) -> None:
        """Without persistent_volume, no named volume is mounted."""
        cmd = self._make_config().build_windows_run_cmd("make")
        vols = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
        self.assertFalse(any(v.endswith(":/tmp/build") for v in vols))

    def test_persistent_volume_mounted_at_build_dir(self) -> None:
        """An auto-named volume is mounted at the build dir."""
        cmd = self._make_config(persistent_volume=True).build_windows_run_cmd("make")
        vols = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
        self.assertIn(f"{self._workspace.name}-build-", " ".join(vols))
        self.assertTrue(any(v.endswith(":/tmp/build") for v in vols))

    def test_persistent_volume_string_used_verbatim(self) -> None:
        """A string persistent_volume is used as the volume name."""
        cmd = self._make_config(persistent_volume="my-vol").build_windows_run_cmd(
            "make"
        )
        self.assertIn("my-vol:/tmp/build", cmd)

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
        # On Linux, Path("C:/Users\\foo") keeps the backslash as a literal
        # character but str() still starts with "C:/", so the drive-letter
        # pattern matches and the result is always "/c/Users/foo".
        self.assertEqual(result, "/c/Users/foo")

    def test_short_path_no_crash(self) -> None:
        """Very short paths should not crash the function."""
        from nanvix_zutil.docker import (
            _translate_windows_path,  # pyright: ignore[reportPrivateUsage]
        )

        result = _translate_windows_path(Path("ab"))
        self.assertIsInstance(result, str)


class TestVolumeLifecycle(unittest.TestCase):
    """Tests for persistent-volume naming and removal."""

    def _config(self, host: Path, persistent_volume: bool | str) -> DockerConfig:
        return DockerConfig(
            image="img",
            mounts=[Mount(host_path=host, container_path=WORKSPACE_CONTAINER_PATH)],
            persistent_volume=persistent_volume,
        )

    def test_volume_name_none_when_disabled(self) -> None:
        self.assertIsNone(self._config(Path("/ws"), False).volume_name())

    def test_volume_name_is_deterministic(self) -> None:
        a = self._config(Path("/repos/cpython"), True).volume_name()
        b = self._config(Path("/repos/cpython"), True).volume_name()
        self.assertEqual(a, b)
        self.assertIsNotNone(a)
        assert a is not None
        self.assertTrue(a.startswith("cpython-build-"))

    def test_volume_name_differs_per_workspace(self) -> None:
        a = self._config(Path("/repos/cpython"), True).volume_name()
        b = self._config(Path("/repos/sqlite"), True).volume_name()
        self.assertNotEqual(a, b)

    def test_volume_name_string_verbatim(self) -> None:
        self.assertEqual(self._config(Path("/ws"), "pinned").volume_name(), "pinned")

    def test_volume_name_requires_workspace_mount(self) -> None:
        cfg = DockerConfig(image="img", mounts=[], persistent_volume=True)
        with self.assertRaises(ValueError):
            cfg.volume_name()

    @patch("nanvix_zutil.docker.subprocess.run")
    @patch("nanvix_zutil.docker.shutil.which", return_value="/usr/bin/docker")
    def test_remove_build_volume_invokes_docker(
        self, _which: object, mock_run: object
    ) -> None:
        remove_build_volume("my-vol")
        mock_run.assert_called_once_with(  # type: ignore[attr-defined]
            ["docker", "volume", "rm", "--force", "my-vol"], check=False
        )

    @patch("nanvix_zutil.docker.subprocess.run")
    @patch("nanvix_zutil.docker.shutil.which", return_value=None)
    def test_remove_build_volume_noop_without_docker(
        self, _which: object, mock_run: object
    ) -> None:
        remove_build_volume("my-vol")
        mock_run.assert_not_called()  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
