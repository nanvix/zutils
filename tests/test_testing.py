# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# pyright: reportPrivateUsage=false
"""Tests for :mod:`nanvix_zutil.testing`."""

from __future__ import annotations

import subprocess as sp
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanvix_zutil.exitcodes import EXIT_MISSING_DEP
from nanvix_zutil.paths import test_out as _test_out
from nanvix_zutil.testing import StandaloneTest, StandaloneTestFailure


def _make_sysroot(nanvix_root: Path) -> Path:
    """Create ``.nanvix/sysroot/bin`` with plausible tool files (Linux ext)."""
    bin_dir = nanvix_root / ".nanvix" / "sysroot" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("nanvixd.elf", "mkramfs.elf", "mkimage.elf"):
        (bin_dir / name).write_bytes(b"")
    return bin_dir


def _make_elf(tmp_path: Path, name: str = "example.elf") -> Path:
    elf = tmp_path / name
    elf.write_bytes(b"")
    return elf


def _fake_make_initrd(*_args: object, **_kwargs: object) -> Path:
    """Stand-in for make_initrd; drops a dummy file in test_out()."""
    out = _test_out() / "example.img"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"")
    return out


def _fake_mkramfs(*_args: object, **_kwargs: object) -> None:
    """Stand-in for mkramfs; the helper's own tests cover its plumbing."""
    return None


def _proc(rc: int) -> sp.CompletedProcess[bytes]:
    return sp.CompletedProcess([], rc)


def _ok_proc(*_a: object, **_kw: object) -> sp.CompletedProcess[bytes]:
    return _proc(0)


def _fail_proc(*_a: object, **_kw: object) -> sp.CompletedProcess[bytes]:
    return _proc(5)


class TestStandaloneTest(unittest.TestCase):
    """Exercise StandaloneTest.run() with mocked subprocess plumbing."""

    def setUp(self) -> None:
        self.tmp = Path.cwd()
        self.bin_dir = _make_sysroot(self.tmp)
        self.elf = _make_elf(self.tmp)

    def _patch_runtime(
        self,
        *,
        subprocess_side_effect: object = None,
        make_initrd: object = _fake_make_initrd,
        mkramfs: object = _fake_mkramfs,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Patch subprocess.run + make_initrd + mkramfs inside testing."""
        sp_mock = MagicMock(side_effect=subprocess_side_effect or _ok_proc)
        initrd_mock = MagicMock(side_effect=make_initrd)
        mkramfs_mock = MagicMock(side_effect=mkramfs)
        self.addCleanup(patch.stopall)
        patch("nanvix_zutil.testing.subprocess.run", sp_mock).start()
        patch("nanvix_zutil.testing.make_initrd", initrd_mock).start()
        patch("nanvix_zutil.testing.mkramfs", mkramfs_mock).start()
        patch("nanvix_zutil.testing.is_windows", return_value=False).start()
        return sp_mock, initrd_mock, mkramfs_mock

    # ---------- happy path ----------

    def test_success_returns_none(self) -> None:
        sp_mock, _, mkramfs_mock = self._patch_runtime()
        StandaloneTest(elf_path=self.elf).run()  # no raise = pass
        self.assertEqual(mkramfs_mock.call_count, 1)
        self.assertEqual(sp_mock.call_count, 1)
        cmd = sp_mock.call_args.args[0]
        self.assertIn("nanvixd.elf", cmd[0])
        self.assertIn("-bin-dir", cmd)
        self.assertIn("-ramfs", cmd)

    def test_windows_selects_exe_extension(self) -> None:
        for f in self.bin_dir.iterdir():
            f.unlink()
        for name in ("nanvixd.exe", "mkramfs.exe", "mkimage.exe"):
            (self.bin_dir / name).write_bytes(b"")

        sp_mock = MagicMock(return_value=_proc(0))
        self.addCleanup(patch.stopall)
        patch("nanvix_zutil.testing.subprocess.run", sp_mock).start()
        patch(
            "nanvix_zutil.testing.make_initrd", MagicMock(side_effect=_fake_make_initrd)
        ).start()
        patch(
            "nanvix_zutil.testing.mkramfs", MagicMock(side_effect=_fake_mkramfs)
        ).start()
        patch("nanvix_zutil.testing.is_windows", return_value=True).start()

        StandaloneTest(elf_path=self.elf).run()
        self.assertIn("nanvixd.exe", sp_mock.call_args.args[0][0])

    # ---------- failure translation ----------

    def test_nonzero_exit_raises_typed_failure(self) -> None:
        self._patch_runtime(subprocess_side_effect=_fail_proc)
        case = StandaloneTest(elf_path=self.elf, label="mytest")
        with self.assertRaises(StandaloneTestFailure) as ctx:
            case.run()
        self.assertIs(ctx.exception.case, case)
        self.assertEqual(ctx.exception.returncode, 5)
        self.assertEqual(ctx.exception.reason, "exited 5")
        self.assertEqual(str(ctx.exception), "mytest: exited 5")

    def test_timeout_raises_typed_failure(self) -> None:
        def _timeout(*_a: object, **_kw: object) -> sp.CompletedProcess[bytes]:
            raise sp.TimeoutExpired(cmd="nanvixd", timeout=120)

        self._patch_runtime(subprocess_side_effect=_timeout)
        case = StandaloneTest(elf_path=self.elf, timeout=120)
        with self.assertRaises(StandaloneTestFailure) as ctx:
            case.run()
        self.assertIsNone(ctx.exception.returncode)
        self.assertEqual(ctx.exception.reason, "timed out after 120s")
        # Finally must still run on timeout.
        self.assertFalse((_test_out() / "example.img").exists())

    def test_oserror_raises_typed_failure(self) -> None:
        # e.g. permission-denied, non-executable ELF at exec time.
        def _oserror(*_a: object, **_kw: object) -> sp.CompletedProcess[bytes]:
            raise PermissionError(13, "Permission denied")

        self._patch_runtime(subprocess_side_effect=_oserror)
        case = StandaloneTest(elf_path=self.elf)
        with self.assertRaises(StandaloneTestFailure) as ctx:
            case.run()
        self.assertIsNone(ctx.exception.returncode)
        self.assertIn("failed to execute", ctx.exception.reason)

    # ---------- fatal preconditions (env, not test failures) ----------

    def test_missing_nanvixd_is_fatal(self) -> None:
        (self.bin_dir / "nanvixd.elf").unlink()
        self._patch_runtime()
        with self.assertRaises(SystemExit) as ctx:
            StandaloneTest(elf_path=self.elf).run()
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)

    def test_missing_elf_is_fatal(self) -> None:
        self._patch_runtime()
        with self.assertRaises(SystemExit) as ctx:
            StandaloneTest(elf_path=self.tmp / "nope.elf").run()
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)

    def test_make_initrd_fatal_is_not_swallowed(self) -> None:
        def _boom(*_a: object, **_kw: object) -> Path:
            raise SystemExit(EXIT_MISSING_DEP)

        self._patch_runtime(make_initrd=_boom)
        with self.assertRaises(SystemExit) as ctx:
            StandaloneTest(elf_path=self.elf).run()
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)

    def test_mkramfs_fatal_is_not_swallowed(self) -> None:
        def _boom(*_a: object, **_kw: object) -> None:
            raise SystemExit(EXIT_MISSING_DEP)

        self._patch_runtime(mkramfs=_boom)
        with self.assertRaises(SystemExit) as ctx:
            StandaloneTest(elf_path=self.elf).run()
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)
        # Finally must still run on fatal.
        self.assertFalse((_test_out() / "example.img").exists())

    # ---------- payload plumbing ----------

    def test_stdin_forwarded_to_nanvixd(self) -> None:
        sp_mock, _, _ = self._patch_runtime()
        payload = b"SELECT 1;\n"
        StandaloneTest(elf_path=self.elf, stdin=payload).run()
        kwargs = sp_mock.call_args.kwargs
        self.assertEqual(kwargs.get("input"), payload)
        self.assertIs(kwargs.get("text"), False)

    def test_no_stdin_uses_text_mode(self) -> None:
        sp_mock, _, _ = self._patch_runtime()
        StandaloneTest(elf_path=self.elf).run()
        kwargs = sp_mock.call_args.kwargs
        self.assertIsNone(kwargs.get("input"))
        self.assertIs(kwargs.get("text"), True)

    def test_ramfs_files_forwarded_to_mkramfs(self) -> None:
        host = self.tmp / "sample.ref"
        host.write_bytes(b"hello")
        _, _, mkramfs_mock = self._patch_runtime()
        files = {"tmp/sample.ref": host, "data/nested/file": host}
        StandaloneTest(elf_path=self.elf, ramfs_files=files).run()
        self.assertEqual(mkramfs_mock.call_args.kwargs.get("files"), files)

    def test_app_args_forwarded_to_initrd(self) -> None:
        _, initrd_mock, _ = self._patch_runtime()
        StandaloneTest(elf_path=self.elf, app_args=("-k", "-f", "/tmp/x")).run()
        self.assertEqual(
            initrd_mock.call_args.kwargs["args"].app_args, ["-k", "-f", "/tmp/x"]
        )

    def test_timeout_forwarded_to_nanvixd(self) -> None:
        sp_mock, _, _ = self._patch_runtime()
        StandaloneTest(elf_path=self.elf, timeout=180).run()
        self.assertEqual(sp_mock.call_args.kwargs.get("timeout"), 180)

    def test_label_overrides_default_name(self) -> None:
        case = StandaloneTest(elf_path=self.elf, label="compress-sample1")
        self.assertEqual(case.name, "compress-sample1")

    # ---------- cleanup ----------

    def test_initrd_removed_after_success(self) -> None:
        self._patch_runtime()
        StandaloneTest(elf_path=self.elf).run()
        self.assertFalse((_test_out() / "example.img").exists())

    def test_initrd_removed_after_test_failure(self) -> None:
        self._patch_runtime(subprocess_side_effect=_fail_proc)
        with self.assertRaises(StandaloneTestFailure):
            StandaloneTest(elf_path=self.elf).run()
        self.assertFalse((_test_out() / "example.img").exists())


if __name__ == "__main__":
    unittest.main()
