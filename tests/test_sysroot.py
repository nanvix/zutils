# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.sysroot."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.config import DEFAULT_TARGET
from nanvix_zutil.sysroot import Sysroot


def _make_tar_bz2(members: dict[str, bytes]) -> bytes:
    """Return a ``.tar.bz2`` archive containing the given *members*.

    Args:
        members: Mapping of archive member name → file contents.

    Returns:
        Compressed archive bytes.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestSysrootDownloadSkipsIfExists(unittest.TestCase):
    """Sysroot.download() returns immediately if the directory already exists."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_skips_download_when_dest_exists(self) -> None:
        dest = Path(self._tmpdir.name) / "sysroot"
        dest.mkdir()

        with patch("nanvix_zutil.github.download_release_asset") as mock_dl:
            sysroot = Sysroot.download(
                machine="hyperlight",
                deployment_mode="multi-process",
                memory_size="128mb",
                tag="v1.0.0",
                dest=dest,
            )
            mock_dl.assert_not_called()

        self.assertEqual(sysroot.path, dest.resolve())
        self.assertEqual(sysroot.tag, "")


class TestSysrootDownloadFetches(unittest.TestCase):
    """Sysroot.download() fetches and extracts the archive on a cache miss."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)
        self._resolve_patcher = patch(
            "nanvix_zutil.github.resolve_release",
            return_value={"target_commitish": "abc1234def5678"},
        )
        self._resolve_patcher.start()

    def tearDown(self) -> None:
        self._resolve_patcher.stop()
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_downloads_and_extracts(self) -> None:
        dest = Path(self._tmpdir.name) / "sysroot"
        archive = _make_tar_bz2({"lib/libposix.a": b"posix-lib"})
        archive_path = Path(self._tmpdir.name) / "nanvix.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            sysroot = Sysroot.download(
                machine="hyperlight",
                deployment_mode="multi-process",
                memory_size="128mb",
                tag="v1.0.0",
                dest=dest,
            )

        self.assertTrue(sysroot.path.is_dir())
        self.assertTrue((sysroot.path / "lib" / "libposix.a").exists())

    def test_asset_name_interpolated_correctly(self) -> None:
        dest = Path(self._tmpdir.name) / "sysroot"
        archive = _make_tar_bz2({})
        archive_path = Path(self._tmpdir.name) / "nanvix.tar.bz2"
        archive_path.write_bytes(archive)

        captured: list[str] = []
        captured_kwargs: list[dict[str, object]] = []

        def fake_download(
            repo: str,
            version_specifier: str | int,
            asset_name: str,
            dest: Path,
            gh_token: str | None = None,
            *,
            match_prefix: bool = False,
            semver: bool = False,
            _release: dict[str, object] | None = None,
        ) -> Path:
            captured.append(asset_name)
            captured_kwargs.append({"match_prefix": match_prefix})
            return archive_path

        with patch(
            "nanvix_zutil.github.download_release_asset",
            side_effect=fake_download,
        ):
            Sysroot.download(
                machine="microvm",
                deployment_mode="standalone",
                memory_size="256mb",
                tag="v2.0.0",
                dest=dest,
            )

        self.assertEqual(
            captured[0],
            f"nanvix-{DEFAULT_TARGET}-microvm-standalone-release-256mb",
        )
        self.assertTrue(captured_kwargs[0]["match_prefix"])

    def test_path_is_absolute(self) -> None:
        dest = Path(self._tmpdir.name) / "sysroot"
        archive = _make_tar_bz2({})
        archive_path = Path(self._tmpdir.name) / "nanvix.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            sysroot = Sysroot.download(
                machine="hyperlight",
                deployment_mode="multi-process",
                memory_size="128mb",
                tag="v1.0.0",
                dest=dest,
            )

        self.assertTrue(sysroot.path.is_absolute())


class TestSysrootVerify(unittest.TestCase):
    """Sysroot.verify() checks that required files exist."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_verify_passes_when_files_present(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        sysroot_dir.mkdir()
        (sysroot_dir / "libposix.a").write_bytes(b"")
        sysroot = Sysroot(sysroot_dir)
        # Should not raise.
        sysroot.verify(required_files=["libposix.a"])

    def test_verify_exits_3_when_file_missing(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        sysroot_dir.mkdir()
        sysroot = Sysroot(sysroot_dir)
        with self.assertRaises(SystemExit) as ctx:
            sysroot.verify(required_files=["libposix.a"])
        self.assertEqual(ctx.exception.code, 3)

    def test_verify_nested_path(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        nested = sysroot_dir / "lib"
        nested.mkdir(parents=True)
        (nested / "libposix.a").write_bytes(b"")
        sysroot = Sysroot(sysroot_dir)
        sysroot.verify(required_files=["lib/libposix.a"])

    def test_verify_empty_list_passes(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        sysroot_dir.mkdir()
        sysroot = Sysroot(sysroot_dir)
        sysroot.verify(required_files=[])


class TestSysrootOverlayLocal(unittest.TestCase):
    """Sysroot.overlay_local() copies local artifacts into the sysroot."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_overlay_copies_bin_files(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        (sysroot_dir / "bin").mkdir(parents=True)
        (sysroot_dir / "bin" / "nanvixd.elf").write_bytes(b"old")

        local_dir = Path(self._tmpdir.name) / "local"
        (local_dir / "bin").mkdir(parents=True)
        (local_dir / "bin" / "nanvixd.elf").write_bytes(b"new-local")

        sysroot = Sysroot(sysroot_dir)
        sysroot.overlay_local_nanvix(local_dir)

        self.assertEqual(
            (sysroot_dir / "bin" / "nanvixd.elf").read_bytes(), b"new-local"
        )

    def test_overlay_copies_lib_files(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        (sysroot_dir / "lib").mkdir(parents=True)
        (sysroot_dir / "lib" / "libposix.a").write_bytes(b"old-lib")

        local_dir = Path(self._tmpdir.name) / "local"
        (local_dir / "lib").mkdir(parents=True)
        (local_dir / "lib" / "libposix.a").write_bytes(b"new-lib")

        sysroot = Sysroot(sysroot_dir)
        sysroot.overlay_local_nanvix(local_dir)

        self.assertEqual((sysroot_dir / "lib" / "libposix.a").read_bytes(), b"new-lib")

    def test_overlay_adds_new_files(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        (sysroot_dir / "bin").mkdir(parents=True)

        local_dir = Path(self._tmpdir.name) / "local"
        (local_dir / "bin").mkdir(parents=True)
        (local_dir / "bin" / "uservm.elf").write_bytes(b"uservm-data")

        sysroot = Sysroot(sysroot_dir)
        sysroot.overlay_local_nanvix(local_dir)

        self.assertTrue((sysroot_dir / "bin" / "uservm.elf").exists())
        self.assertEqual(
            (sysroot_dir / "bin" / "uservm.elf").read_bytes(), b"uservm-data"
        )

    def test_overlay_no_artifacts_warns(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        sysroot_dir.mkdir()

        local_dir = Path(self._tmpdir.name) / "local"
        local_dir.mkdir()  # No bin/ or lib/ subdirs

        sysroot = Sysroot(sysroot_dir)
        # Should not raise, just warn.
        sysroot.overlay_local_nanvix(local_dir)

    def test_overlay_nonexistent_path_exits(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "sysroot"
        sysroot_dir.mkdir()
        log_mod.set_json_mode(True)

        sysroot = Sysroot(sysroot_dir)
        with self.assertRaises(SystemExit) as ctx:
            sysroot.overlay_local_nanvix(Path("/nonexistent/path"))
        self.assertEqual(ctx.exception.code, 3)


class TestSysrootFromLocal(unittest.TestCase):
    """Sysroot.from_local() uses a directory as-is, no download."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_returns_sysroot_pointing_at_path(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "my-sysroot"
        sysroot_dir.mkdir()

        sysroot = Sysroot.from_local(sysroot_dir)

        self.assertEqual(sysroot.path, sysroot_dir.resolve())
        self.assertEqual(sysroot.tag, "")

    def test_does_not_call_github(self) -> None:
        sysroot_dir = Path(self._tmpdir.name) / "my-sysroot"
        sysroot_dir.mkdir()

        with patch("nanvix_zutil.github.resolve_release") as mock_resolve:
            with patch("nanvix_zutil.github.download_release_asset") as mock_dl:
                Sysroot.from_local(sysroot_dir)
                mock_resolve.assert_not_called()
                mock_dl.assert_not_called()

    def test_exits_if_path_not_directory(self) -> None:
        log_mod.set_json_mode(True)
        bad_path = Path(self._tmpdir.name) / "nonexistent"

        with self.assertRaises(SystemExit) as ctx:
            Sysroot.from_local(bad_path)
        self.assertEqual(ctx.exception.code, 3)

    def test_exits_if_path_is_file(self) -> None:
        log_mod.set_json_mode(True)
        file_path = Path(self._tmpdir.name) / "a-file"
        file_path.write_text("not a dir")

        with self.assertRaises(SystemExit) as ctx:
            Sysroot.from_local(file_path)
        self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
