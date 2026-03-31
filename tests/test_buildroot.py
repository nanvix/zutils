# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.buildroot."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.buildroot import Buildroot, Dependency, Ref, RefKind


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


class TestDependency(unittest.TestCase):
    """Dependency dataclass behaves correctly."""

    def test_required_fields(self) -> None:
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        self.assertEqual(dep.name, "zlib")
        self.assertEqual(dep.repo, "nanvix/zlib")
        self.assertEqual(dep.ref.value, "v1.0.0")

    def test_default_artifact_pattern(self) -> None:
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        expected = "{name}-{machine}-{mode}-{mem}.tar.bz2"
        self.assertEqual(dep.artifact_pattern, expected)

    def test_default_install_libs_none(self) -> None:
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        self.assertIsNone(dep.install_libs)

    def test_default_install_headers_none(self) -> None:
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        self.assertIsNone(dep.install_headers)

    def test_custom_artifact_pattern(self) -> None:
        dep = Dependency(
            name="foo",
            repo="nanvix/foo",
            ref=Ref(kind=RefKind.TAG, value="v2.0.0"),
            artifact_pattern="{name}.tar.bz2",
        )
        self.assertEqual(dep.artifact_pattern, "{name}.tar.bz2")


class TestBuildrootCreate(unittest.TestCase):
    """Buildroot.create() sets up the expected directory layout."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_creates_lib_dir(self) -> None:
        dest = Path(self._tmpdir.name) / "br"
        br = Buildroot.create(dest=dest)
        self.assertTrue((br.path / "lib").is_dir())

    def test_creates_include_dir(self) -> None:
        dest = Path(self._tmpdir.name) / "br"
        br = Buildroot.create(dest=dest)
        self.assertTrue((br.path / "include").is_dir())

    def test_path_is_absolute(self) -> None:
        dest = Path(self._tmpdir.name) / "br"
        br = Buildroot.create(dest=dest)
        self.assertTrue(br.path.is_absolute())

    def test_idempotent(self) -> None:
        dest = Path(self._tmpdir.name) / "br"
        br1 = Buildroot.create(dest=dest)
        br2 = Buildroot.create(dest=dest)
        self.assertEqual(br1.path, br2.path)


class TestBuildrootVerify(unittest.TestCase):
    """Buildroot.verify() checks that required libraries exist."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_verify_passes_when_libs_present(self) -> None:
        dest = Path(self._tmpdir.name) / "br"
        br = Buildroot.create(dest=dest)
        (br.path / "lib" / "libz.a").write_bytes(b"")
        # Should not raise.
        br.verify(required_libs=["libz.a"])

    def test_verify_exits_3_when_lib_missing(self) -> None:
        dest = Path(self._tmpdir.name) / "br"
        br = Buildroot.create(dest=dest)
        with self.assertRaises(SystemExit) as ctx:
            br.verify(required_libs=["libposix.a"])
        self.assertEqual(ctx.exception.code, 3)

    def test_verify_empty_list_passes(self) -> None:
        dest = Path(self._tmpdir.name) / "br"
        br = Buildroot.create(dest=dest)
        br.verify(required_libs=[])


class TestBuildrootInstallDep(unittest.TestCase):
    """Buildroot.install_dep() extracts libs and headers correctly."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def _setup_buildroot(self) -> Buildroot:
        dest = Path(self._tmpdir.name) / "br"
        return Buildroot.create(dest=dest)

    def test_install_dep_extracts_lib(self) -> None:
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "sysroot/lib/libz.a": b"lib-content",
                "sysroot/include/zlib.h": b"header-content",
            }
        )
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=Path(self._tmpdir.name) / "zlib.tar.bz2",
        ) as mock_dl:
            # Write the fake archive so tarfile.open can read it.
            archive_path = Path(self._tmpdir.name) / "zlib.tar.bz2"
            archive_path.write_bytes(archive)
            mock_dl.return_value = archive_path

            br.install_dep(dep)

        self.assertTrue((br.path / "lib" / "libz.a").exists())

    def test_install_dep_extracts_header(self) -> None:
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "sysroot/lib/libz.a": b"lib-content",
                "sysroot/include/zlib.h": b"header-content",
            }
        )
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        archive_path = Path(self._tmpdir.name) / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((br.path / "include" / "zlib.h").exists())

    def test_install_dep_selective_libs(self) -> None:
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "sysroot/lib/libz.a": b"libz",
                "sysroot/lib/libextra.a": b"libextra",
            }
        )
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
            install_libs=["libz.a"],
        )
        archive_path = Path(self._tmpdir.name) / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((br.path / "lib" / "libz.a").exists())
        self.assertFalse((br.path / "lib" / "libextra.a").exists())

    def test_install_dep_selective_headers(self) -> None:
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "sysroot/include/zlib.h": b"wanted",
                "sysroot/include/internal.h": b"not-wanted",
            }
        )
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
            install_headers=["zlib.h"],
        )
        archive_path = Path(self._tmpdir.name) / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((br.path / "include" / "zlib.h").exists())
        self.assertFalse((br.path / "include" / "internal.h").exists())

    def test_install_dep_artifact_name_interpolated(self) -> None:
        br = self._setup_buildroot()
        archive = _make_tar_bz2({"sysroot/lib/libz.a": b""})
        archive_path = Path(self._tmpdir.name) / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )

        captured: list[str] = []

        def fake_download(
            repo: str,
            version_specifier: str | int,
            asset_name: str,
            dest: Path,
            gh_token: str | None = None,
        ) -> Path:
            captured.append(asset_name)
            return archive_path

        with patch(
            "nanvix_zutil.github.download_release_asset", side_effect=fake_download
        ):
            br.install_dep(
                dep,
                machine="microvm",
                deployment_mode="single-process",
                memory_size="256mb",
            )

        self.assertEqual(captured[0], "zlib-microvm-single-process-256mb.tar.bz2")

    def test_install_dep_preserves_header_subdirectory(self) -> None:
        """Headers in subdirectories are extracted with directory structure preserved."""
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "sysroot/include/openssl/ssl.h": b"ssl-header",
                "sysroot/include/openssl/crypto.h": b"crypto-header",
                "sysroot/lib/libssl.a": b"ssl-lib",
            }
        )
        dep = Dependency(
            name="openssl",
            repo="nanvix/openssl",
            ref=Ref(kind=RefKind.TAG, value="v3.5.0"),
        )
        archive_path = Path(self._tmpdir.name) / "openssl.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((br.path / "include" / "openssl" / "ssl.h").exists())
        self.assertTrue((br.path / "include" / "openssl" / "crypto.h").exists())
        self.assertTrue((br.path / "lib" / "libssl.a").exists())
        # Verify headers are NOT flattened to include/ssl.h
        self.assertFalse((br.path / "include" / "ssl.h").exists())

    def test_install_dep_preserves_lib_subdirectory(self) -> None:
        """Libraries in subdirectories are extracted with directory structure preserved."""
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "sysroot/lib/engines/libcapi.a": b"engine-lib",
                "sysroot/lib/libssl.a": b"ssl-lib",
            }
        )
        dep = Dependency(
            name="openssl",
            repo="nanvix/openssl",
            ref=Ref(kind=RefKind.TAG, value="v3.5.0"),
        )
        archive_path = Path(self._tmpdir.name) / "openssl.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((br.path / "lib" / "engines" / "libcapi.a").exists())
        self.assertTrue((br.path / "lib" / "libssl.a").exists())

    def test_install_dep_flat_tarball_without_segments(self) -> None:
        """Tarballs with bare filenames (no include/ or lib/ segment) still work."""
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "libz.a": b"lib-content",
                "zlib.h": b"header-content",
            }
        )
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
        )
        archive_path = Path(self._tmpdir.name) / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((br.path / "lib" / "libz.a").exists())
        self.assertTrue((br.path / "include" / "zlib.h").exists())


if __name__ == "__main__":
    unittest.main()
