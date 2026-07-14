# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for :func:`nanvix_zutil.helpers.mkramfs`."""

from __future__ import annotations

import subprocess as sp
import unittest
from pathlib import Path
from unittest.mock import patch

from nanvix_zutil import helpers
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP


def _make_sysroot(nanvix_root: Path) -> Path:
    """Create ``.nanvix/sysroot/bin`` with plausible tool files (Linux ext)."""
    bin_dir = nanvix_root / ".nanvix" / "sysroot" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("mkramfs.elf", "mkramfs.exe"):
        (bin_dir / name).write_bytes(b"")
    return bin_dir


class TestMkramfsHelper(unittest.TestCase):
    """helpers.mkramfs stages files and invokes the sysroot tool."""

    def setUp(self) -> None:
        import os
        import tempfile

        from nanvix_zutil.paths import nanvix_root

        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)

        self._orig_cwd = Path.cwd()
        self.addCleanup(os.chdir, self._orig_cwd)

        os.chdir(self._td.name)

        nanvix_root.cache_clear()
        self.addCleanup(nanvix_root.cache_clear)

        self.tmp = Path(self._td.name)
        self.bin_dir = _make_sysroot(self.tmp)

    def test_stages_files_and_ensures_tmp(self) -> None:
        host = self.tmp / "sample.ref"
        host.write_bytes(b"hello")

        seen: list[tuple[str, bool]] = []

        def spy(*args: str, **_kw: object) -> sp.CompletedProcess[str]:
            # mkramfs invoked as: <tool> -o <output> <staging>
            staging = Path(args[3])
            seen.append(("tmp/sample.ref", (staging / "tmp/sample.ref").is_file()))
            seen.append(("data/nested/file", (staging / "data/nested/file").is_file()))
            seen.append(("tmp/", (staging / "tmp").is_dir()))
            return sp.CompletedProcess(list(args), 0)

        with patch("nanvix_zutil.helpers.run", side_effect=spy):
            helpers.mkramfs(
                self.tmp / "out.img",
                files={"tmp/sample.ref": host, "data/nested/file": host},
            )
        self.assertEqual(
            seen,
            [
                ("tmp/sample.ref", True),
                ("data/nested/file", True),
                ("tmp/", True),
            ],
        )

    def test_ensures_tmp_when_no_files(self) -> None:
        seen: dict[str, bool] = {}

        def spy(*args: str, **_kw: object) -> sp.CompletedProcess[str]:
            staging = Path(args[3])
            seen["tmp"] = (staging / "tmp").is_dir()
            return sp.CompletedProcess(list(args), 0)

        with patch("nanvix_zutil.helpers.run", side_effect=spy):
            helpers.mkramfs(self.tmp / "out.img")
        self.assertTrue(seen.get("tmp"))

    def test_missing_tool_is_fatal(self) -> None:
        (self.bin_dir / "mkramfs.elf").unlink()
        with patch("nanvix_zutil.helpers.is_windows", return_value=False):
            with self.assertRaises(SystemExit) as ctx:
                helpers.mkramfs(self.tmp / "out.img")
        self.assertEqual(ctx.exception.code, EXIT_MISSING_DEP)


if __name__ == "__main__":
    unittest.main()
