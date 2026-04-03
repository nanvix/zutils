# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.release."""

import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from nanvix_zutil.exitcodes import EXIT_GENERAL_ERROR
from nanvix_zutil.release import (
    DEFAULT_FORMATS,
    ArchiveFormat,
    _build_tarball,  # pyright: ignore[reportPrivateUsage]
    _build_zip,  # pyright: ignore[reportPrivateUsage]
    package,
)


def _make_source_tree(root: Path) -> None:
    """Create a small directory tree for archiving tests.

    Layout::

        root/
            hello.txt
            sub/
                data.bin
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "hello.txt").write_text("hello world\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "data.bin").write_bytes(b"\x00\x01\x02\x03")


class TestArchiveFormat(unittest.TestCase):
    """Tests for the ArchiveFormat enum."""

    def test_extension_tar_gz(self) -> None:
        self.assertEqual(ArchiveFormat.TAR_GZ.extension, ".tar.gz")

    def test_extension_tar_bz2(self) -> None:
        self.assertEqual(ArchiveFormat.TAR_BZ2.extension, ".tar.bz2")

    def test_extension_zip(self) -> None:
        self.assertEqual(ArchiveFormat.ZIP.extension, ".zip")

    def test_value_tar_gz(self) -> None:
        self.assertEqual(ArchiveFormat.TAR_GZ.value, "tar.gz")

    def test_value_tar_bz2(self) -> None:
        self.assertEqual(ArchiveFormat.TAR_BZ2.value, "tar.bz2")

    def test_value_zip(self) -> None:
        self.assertEqual(ArchiveFormat.ZIP.value, "zip")


class TestDefaultFormats(unittest.TestCase):
    """Tests for DEFAULT_FORMATS."""

    def test_contains_tar_gz(self) -> None:
        self.assertIn(ArchiveFormat.TAR_GZ, DEFAULT_FORMATS)

    def test_contains_zip(self) -> None:
        self.assertIn(ArchiveFormat.ZIP, DEFAULT_FORMATS)

    def test_does_not_contain_tar_bz2(self) -> None:
        self.assertNotIn(ArchiveFormat.TAR_BZ2, DEFAULT_FORMATS)

    def test_is_tuple(self) -> None:
        self.assertIsInstance(DEFAULT_FORMATS, tuple)


class TestBuildTarball(unittest.TestCase):
    """Tests for _build_tarball (internal helper)."""

    def test_tar_gz_contains_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            out = Path(tmp) / "out.tar.gz"
            result = _build_tarball(src, out, "gz")
            self.assertEqual(result, out)
            self.assertTrue(out.exists())
            with tarfile.open(out, "r:gz") as tf:
                names = sorted(tf.getnames())
            self.assertIn("hello.txt", names)
            self.assertIn("sub/data.bin", names)

    def test_tar_bz2_contains_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            out = Path(tmp) / "out.tar.bz2"
            result = _build_tarball(src, out, "bz2")
            self.assertEqual(result, out)
            self.assertTrue(out.exists())
            with tarfile.open(out, "r:bz2") as tf:
                names = sorted(tf.getnames())
            self.assertIn("hello.txt", names)
            self.assertIn("sub/data.bin", names)

    def test_tarball_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            out = Path(tmp) / "out.tar.gz"
            _build_tarball(src, out, "gz")
            with tarfile.open(out, "r:gz") as tf:
                member = tf.extractfile("hello.txt")
                self.assertIsNotNone(member)
                assert member is not None  # for type checker
                self.assertEqual(member.read(), b"hello world\n")


class TestBuildZip(unittest.TestCase):
    """Tests for _build_zip (internal helper)."""

    def test_zip_contains_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            out = Path(tmp) / "out.zip"
            result = _build_zip(src, out)
            self.assertEqual(result, out)
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out, "r") as zf:
                names = sorted(zf.namelist())
            self.assertIn("hello.txt", names)
            self.assertIn("sub/data.bin", names)

    def test_zip_does_not_include_directories_as_entries(self) -> None:
        """_build_zip only adds files, not directory entries."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            out = Path(tmp) / "out.zip"
            _build_zip(src, out)
            with zipfile.ZipFile(out, "r") as zf:
                for name in zf.namelist():
                    self.assertFalse(
                        name.endswith("/"), f"unexpected dir entry: {name}"
                    )

    def test_zip_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            out = Path(tmp) / "out.zip"
            _build_zip(src, out)
            with zipfile.ZipFile(out, "r") as zf:
                self.assertEqual(zf.read("hello.txt"), b"hello world\n")
                self.assertEqual(zf.read("sub/data.bin"), b"\x00\x01\x02\x03")


class TestPackage(unittest.TestCase):
    """Tests for the public package() function."""

    def test_default_formats(self) -> None:
        """package() with default formats produces .tar.gz and .zip."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            dest = Path(tmp) / "dist"
            result = package(src, dest, "mylib-v1.0")
            self.assertEqual(len(result), 2)
            names = [p.name for p in result]
            self.assertIn("mylib-v1.0.tar.gz", names)
            self.assertIn("mylib-v1.0.zip", names)

    def test_single_format_tar_bz2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            dest = Path(tmp) / "dist"
            result = package(src, dest, "pkg", formats=(ArchiveFormat.TAR_BZ2,))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].name, "pkg.tar.bz2")

    def test_all_three_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            dest = Path(tmp) / "dist"
            all_fmts = (ArchiveFormat.TAR_GZ, ArchiveFormat.TAR_BZ2, ArchiveFormat.ZIP)
            result = package(src, dest, "all", formats=all_fmts)
            self.assertEqual(len(result), 3)
            names = {p.name for p in result}
            self.assertEqual(names, {"all.tar.gz", "all.tar.bz2", "all.zip"})

    def test_creates_dest_directory(self) -> None:
        """package() creates the destination directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            dest = Path(tmp) / "nested" / "output" / "dist"
            self.assertFalse(dest.exists())
            package(src, dest, "test")
            self.assertTrue(dest.exists())

    def test_returns_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            dest = Path(tmp) / "dist"
            result = package(src, dest, "abs")
            for p in result:
                self.assertTrue(p.is_absolute(), f"expected absolute: {p}")

    def test_missing_source_exits(self) -> None:
        """package() calls log.fatal when source doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "nonexistent"
            dest = Path(tmp) / "dist"
            with self.assertRaises(SystemExit) as ctx:
                package(src, dest, "fail")
            self.assertEqual(ctx.exception.code, EXIT_GENERAL_ERROR)

    def test_source_is_file_exits(self) -> None:
        """package() calls log.fatal when source is a file, not a directory."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "afile.txt"
            src.write_text("not a directory")
            dest = Path(tmp) / "dist"
            with self.assertRaises(SystemExit) as ctx:
                package(src, dest, "fail")
            self.assertEqual(ctx.exception.code, EXIT_GENERAL_ERROR)

    def test_zip_and_tarball_have_same_file_set(self) -> None:
        """Both archive types should contain the same set of files."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            dest = Path(tmp) / "dist"
            result = package(src, dest, "match")

            tar_path = [p for p in result if p.name.endswith(".tar.gz")][0]
            zip_path = [p for p in result if p.name.endswith(".zip")][0]

            with tarfile.open(tar_path, "r:gz") as tf:
                tar_files = {n for n in tf.getnames() if not tf.getmember(n).isdir()}
            with zipfile.ZipFile(zip_path, "r") as zf:
                zip_files = set(zf.namelist())

            self.assertEqual(tar_files, zip_files)

    def test_order_matches_formats(self) -> None:
        """Returned paths should be in the same order as the formats tuple."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _make_source_tree(src)
            dest = Path(tmp) / "dist"
            fmts = (ArchiveFormat.ZIP, ArchiveFormat.TAR_BZ2, ArchiveFormat.TAR_GZ)
            result = package(src, dest, "order", formats=fmts)
            self.assertEqual(result[0].name, "order.zip")
            self.assertEqual(result[1].name, "order.tar.bz2")
            self.assertEqual(result[2].name, "order.tar.gz")


if __name__ == "__main__":
    unittest.main()
