# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.release."""

import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from nanvix_zutil.release import (
    _build_tarball,  # pyright: ignore[reportPrivateUsage]
    _build_zip,  # pyright: ignore[reportPrivateUsage]
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
    (root / "hello.txt").write_bytes(b"hello world\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "data.bin").write_bytes(b"\x00\x01\x02\x03")


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


class TestBuilderSymlinkSecurity(unittest.TestCase):
    """Symlink handling in the internal archive builders."""

    def _make_external_payload(self, tmp: Path) -> tuple[Path, Path]:
        """Create an external directory tree with sensitive content."""
        external_dir = tmp / "external"
        external_dir.mkdir()
        (external_dir / "secret.txt").write_text("THIS SHOULD NOT BE IN ARCHIVES")
        nested = external_dir / "nested"
        nested.mkdir()
        (nested / "nested_secret.txt").write_text("NESTED SECRET DATA")
        external_file = tmp / "external.txt"
        external_file.write_text("sensitive data")
        return external_dir, external_file

    def test_tarball_excludes_file_symlinks(self) -> None:
        """_build_tarball does not archive symlinks pointing outside source."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            _make_source_tree(src)
            _external_dir, external_file = self._make_external_payload(tmp_path)

            link = src / "dangerous_link"
            try:
                link.symlink_to(external_file)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not supported on this platform")

            out = tmp_path / "out.tar.gz"
            _build_tarball(src, out, "gz")
            with tarfile.open(out, "r:gz") as tf:
                names = set(tf.getnames())
            self.assertNotIn("dangerous_link", names)

    def test_tarball_does_not_traverse_symlinked_dirs(self) -> None:
        """_build_tarball does not descend into symlinked directories."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            _make_source_tree(src)
            external_dir, _external_file = self._make_external_payload(tmp_path)

            link = src / "evil_link"
            try:
                link.symlink_to(external_dir)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not supported on this platform")

            out = tmp_path / "out.tar.gz"
            _build_tarball(src, out, "gz")
            with tarfile.open(out, "r:gz") as tf:
                names = set(tf.getnames())
            self.assertNotIn("evil_link", names)
            self.assertNotIn("evil_link/secret.txt", names)
            self.assertNotIn("evil_link/nested/nested_secret.txt", names)

    def test_zip_excludes_file_symlinks(self) -> None:
        """_build_zip does not archive symlinks pointing outside source."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            _make_source_tree(src)
            _external_dir, external_file = self._make_external_payload(tmp_path)

            link = src / "dangerous_link"
            try:
                link.symlink_to(external_file)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not supported on this platform")

            out = tmp_path / "out.zip"
            _build_zip(src, out)
            with zipfile.ZipFile(out, "r") as zf:
                names = set(zf.namelist())
            self.assertNotIn("dangerous_link", names)

    def test_zip_does_not_traverse_symlinked_dirs(self) -> None:
        """_build_zip does not descend into symlinked directories."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            _make_source_tree(src)
            external_dir, _external_file = self._make_external_payload(tmp_path)

            link = src / "evil_link"
            try:
                link.symlink_to(external_dir)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not supported on this platform")

            out = tmp_path / "out.zip"
            _build_zip(src, out)
            with zipfile.ZipFile(out, "r") as zf:
                names = set(zf.namelist())
            self.assertNotIn("evil_link", names)
            self.assertNotIn("evil_link/", names)
            self.assertNotIn("evil_link/secret.txt", names)
            self.assertNotIn("evil_link/nested/nested_secret.txt", names)


if __name__ == "__main__":
    unittest.main()
