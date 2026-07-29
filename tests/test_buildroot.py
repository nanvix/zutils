# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.buildroot descriptors and Sysroot dep install."""

import io
import stat
import sys
import tarfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from nanvix_zutil.buildroot import (
    Dependency,
    Ref,
    RefKind,
    extract_nanvix_version,
    extract_nanvix_version_base,
    parse_semver_tuple,
    suffix_dep,
)
from nanvix_zutil.paths import sysroot
from nanvix_zutil.release import DEV_ARCHIVE_SUFFIX
from nanvix_zutil.sysroot import Sysroot, ZIP_MODE_SHIFT


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


def _make_zip(members: dict[str, bytes]) -> bytes:
    """Return a ``.zip`` archive containing the given *members*.

    Args:
        members: Mapping of archive member name → file contents.

    Returns:
        Zip archive bytes.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
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
        expected = "{name}-{host}-{arch}-{machine}-{mode}-{mem}" + DEV_ARCHIVE_SUFFIX
        self.assertEqual(dep.artifact_pattern, expected)

    def test_custom_artifact_pattern(self) -> None:
        dep = Dependency(
            name="foo",
            repo="nanvix/foo",
            ref=Ref(kind=RefKind.TAG, value="v2.0.0"),
            artifact_pattern="{name}.tar.bz2",
        )
        self.assertEqual(dep.artifact_pattern, "{name}.tar.bz2")


class TestBuildrootVerify(unittest.TestCase):
    """Sysroot.verify() checks that required files exist."""

    def test_verify_passes_when_file_present(self) -> None:
        br = Sysroot(sysroot())
        (sysroot() / "lib").mkdir(parents=True, exist_ok=True)
        (sysroot() / "lib" / "libz.a").write_bytes(b"")
        # Should not raise.
        br.verify(required_files=["lib/libz.a"])

    def test_verify_passes_for_non_lib_path(self) -> None:
        br = Sysroot(sysroot())
        (sysroot() / "include" / "zlib.h").parent.mkdir(parents=True, exist_ok=True)
        (sysroot() / "include" / "zlib.h").write_bytes(b"")
        br.verify(required_files=["include/zlib.h"])

    def test_verify_exits_3_when_file_missing(self) -> None:
        br = Sysroot(sysroot())
        with self.assertRaises(SystemExit) as ctx:
            br.verify(required_files=["lib/libposix.a"])
        self.assertEqual(ctx.exception.code, 3)

    def test_verify_rejects_absolute_and_traversal(self) -> None:
        br = Sysroot(sysroot())
        for bad in ("/etc/passwd", "../escape.a"):
            with self.assertRaises(SystemExit):
                br.verify(required_files=[bad])

    def test_verify_empty_list_passes(self) -> None:
        br = Sysroot(sysroot())
        br.verify(required_files=[])


class TestBuildrootInstallDep(unittest.TestCase):
    """Buildroot.install_dep() extracts libs and headers correctly."""

    def _setup_buildroot(self) -> Sysroot:
        return Sysroot(sysroot())

    def test_install_dep_extracts_lib(self) -> None:
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "lib/libz.a": b"lib-content",
                "include/zlib.h": b"header-content",
            }
        )
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        archive_path = Path.cwd() / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((sysroot() / "lib" / "libz.a").exists())

    def test_install_dep_extracts_header(self) -> None:
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "lib/libz.a": b"lib-content",
                "include/zlib.h": b"header-content",
            }
        )
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        archive_path = Path.cwd() / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((sysroot() / "include" / "zlib.h").exists())

    def test_install_dep_artifact_name_interpolated(self) -> None:
        br = self._setup_buildroot()
        archive = _make_tar_bz2({"lib/libz.a": b""})
        archive_path = Path.cwd() / "zlib.tar.bz2"
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
            *,
            match_prefix: bool = False,
            semver: bool = False,
            _release: dict[str, object] | None = None,
            allow_missing: bool = False,
        ) -> Path:
            captured.append(asset_name)
            return archive_path

        with patch(
            "nanvix_zutil.sysroot.github.download_release_asset",
            side_effect=fake_download,
        ):
            br.install_dep(
                dep,
                host="linux",
                target="x86",
                machine="microvm",
                deployment_mode="single-process",
                memory_size="256mb",
            )

        self.assertEqual(captured[0], "zlib-linux-x86-microvm-single-process-256mb-dev")

    def test_install_dep_custom_pattern_receives_host_and_arch(self) -> None:
        """Custom ``artifact_pattern`` still receives the new host/arch keys."""
        br = self._setup_buildroot()
        archive = _make_tar_bz2({"lib/libz.a": b""})
        archive_path = Path.cwd() / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
            artifact_pattern="{name}-{host}-{arch}",
        )

        captured: list[str] = []

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
            allow_missing: bool = False,
        ) -> Path:
            captured.append(asset_name)
            return archive_path

        with patch(
            "nanvix_zutil.sysroot.github.download_release_asset",
            side_effect=fake_download,
        ):
            br.install_dep(dep, host="windows", target="arm")

        self.assertEqual(captured[0], "zlib-windows-arm")

    def test_install_dep_fatal_when_asset_missing(self) -> None:
        """A missing ``-dev`` asset must fail hard — no fallback."""
        br = self._setup_buildroot()
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )

        captured_kwargs: dict[str, object] = {}

        def fake_download(*_args: object, **kwargs: object) -> Path:
            captured_kwargs.update(kwargs)
            from nanvix_zutil import log

            log.fatal("Asset 'zlib-...-dev' not found in release nanvix/zlib@v1.0.0")

        with patch(
            "nanvix_zutil.sysroot.github.download_release_asset",
            side_effect=fake_download,
        ):
            with self.assertRaises(SystemExit):
                br.install_dep(dep)

        # Lock the contract: install_dep must never pass allow_missing=True.
        self.assertNotEqual(captured_kwargs.get("allow_missing"), True)

    def test_install_dep_preserves_header_subdirectory(self) -> None:
        """Headers in subdirectories are extracted with directory structure preserved."""
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "include/openssl/ssl.h": b"ssl-header",
                "include/openssl/crypto.h": b"crypto-header",
                "lib/libssl.a": b"ssl-lib",
            }
        )
        dep = Dependency(
            name="openssl",
            repo="nanvix/openssl",
            ref=Ref(kind=RefKind.TAG, value="v3.5.0"),
        )
        archive_path = Path.cwd() / "openssl.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((sysroot() / "include" / "openssl" / "ssl.h").exists())
        self.assertTrue((sysroot() / "include" / "openssl" / "crypto.h").exists())
        self.assertTrue((sysroot() / "lib" / "libssl.a").exists())
        # Verify headers are NOT flattened to include/ssl.h
        self.assertFalse((sysroot() / "include" / "ssl.h").exists())

    def test_install_dep_preserves_lib_subdirectory(self) -> None:
        """Libraries in subdirectories are extracted with directory structure preserved."""
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "lib/engines/libcapi.a": b"engine-lib",
                "lib/libssl.a": b"ssl-lib",
            }
        )
        dep = Dependency(
            name="openssl",
            repo="nanvix/openssl",
            ref=Ref(kind=RefKind.TAG, value="v3.5.0"),
        )
        archive_path = Path.cwd() / "openssl.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((sysroot() / "lib" / "engines" / "libcapi.a").exists())
        self.assertTrue((sysroot() / "lib" / "libssl.a").exists())

    def test_install_dep_copies_arbitrary_top_level_dirs(self) -> None:
        """All top-level dirs (lib/, share/, bin/, …) land verbatim, any file type."""
        br = self._setup_buildroot()
        archive = _make_tar_bz2(
            {
                "lib/libz.so": b"shared",
                "lib/libz.a": b"static",
                "include/zlib.h": b"header",
                "share/man/man1/zlib.1": b"man",
                "bin/zlib-config": b"script",
            }
        )
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
        )
        archive_path = Path.cwd() / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertEqual((sysroot() / "lib" / "libz.so").read_bytes(), b"shared")
        self.assertTrue((sysroot() / "lib" / "libz.a").exists())
        self.assertTrue((sysroot() / "include" / "zlib.h").exists())
        self.assertEqual(
            (sysroot() / "share" / "man" / "man1" / "zlib.1").read_bytes(), b"man"
        )
        self.assertTrue((sysroot() / "bin" / "zlib-config").exists())

    def test_install_dep_resolves_symlink_member(self) -> None:
        """A symlinked tar member lands as a real file copy of its target."""
        br = self._setup_buildroot()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
            data = b"real-lib"
            info = tarfile.TarInfo("lib/libz.so.1")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo("lib/libz.so")
            link.type = tarfile.SYMTYPE
            link.linkname = "libz.so.1"
            tf.addfile(link)
        archive_path = Path.cwd() / "zlib.tar.bz2"
        archive_path.write_bytes(buf.getvalue())
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        link_path = sysroot() / "lib" / "libz.so"
        self.assertTrue(link_path.is_file() and not link_path.is_symlink())
        self.assertEqual(link_path.read_bytes(), b"real-lib")


class TestInstallLocalArchive(unittest.TestCase):
    """Buildroot.install_local_archive() copies a sibling's staged dev tree."""

    def _dep(self, **overrides: object) -> Dependency:
        return Dependency(
            name=str(overrides.pop("name", "zlib")),
            repo=str(overrides.pop("repo", "nanvix/zlib")),
            ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
        )

    def _stage(self, files: dict[str, bytes]) -> Path:
        """Write *files* under a sibling's staging tree; return its manifest path."""
        manifest = Path.cwd() / "zlib_ws" / ".nanvix" / "nanvix.toml"
        dev = manifest.parent / "out" / "staging" / "dev"
        for rel, data in files.items():
            dst = dev / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("")
        return manifest

    def test_copies_lib_and_header(self) -> None:
        br = Sysroot(sysroot())
        manifest = self._stage(
            {
                "lib/libz.a": b"lib-content",
                "include/zlib.h": b"header-content",
            }
        )

        br.install_local_archive(self._dep(), manifest)

        lib = sysroot() / "lib" / "libz.a"
        hdr = sysroot() / "include" / "zlib.h"
        self.assertTrue(lib.is_file() and not lib.is_symlink())
        self.assertTrue(hdr.is_file() and not hdr.is_symlink())
        self.assertEqual(lib.read_bytes(), b"lib-content")
        self.assertEqual(hdr.read_bytes(), b"header-content")

    def test_preserves_header_subdirectory(self) -> None:
        br = Sysroot(sysroot())
        manifest = self._stage(
            {
                "include/openssl/ssl.h": b"ssl",
                "include/openssl/crypto.h": b"crypto",
                "lib/libssl.a": b"lib",
            }
        )

        br.install_local_archive(
            self._dep(name="openssl", repo="nanvix/openssl"), manifest
        )

        self.assertTrue((sysroot() / "include" / "openssl" / "ssl.h").is_file())
        self.assertTrue((sysroot() / "include" / "openssl" / "crypto.h").is_file())
        self.assertTrue((sysroot() / "lib" / "libssl.a").is_file())

    def test_preserves_lib_subdirectory(self) -> None:
        """Nested libs (e.g. ``lib/engines/libcapi.a``) are copied intact."""
        br = Sysroot(sysroot())
        manifest = self._stage(
            {
                "lib/engines/libcapi.a": b"engine",
                "lib/libssl.a": b"lib",
            }
        )

        br.install_local_archive(
            self._dep(name="openssl", repo="nanvix/openssl"), manifest
        )

        self.assertTrue((sysroot() / "lib" / "engines" / "libcapi.a").is_file())
        self.assertTrue((sysroot() / "lib" / "libssl.a").is_file())

    def test_fatals_when_staging_missing(self) -> None:
        br = Sysroot(sysroot())
        manifest_path = Path.cwd() / "zlib_ws" / ".nanvix" / "nanvix.toml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text("")
        with self.assertRaises(SystemExit):
            br.install_local_archive(self._dep(), manifest_path)


class TestSuffixDep(unittest.TestCase):
    """Tests for suffix_dep()."""

    def test_suffixes_version_ref(self) -> None:
        """VERSION ref gets the nanvix suffix appended."""
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.VERSION, value="1.3.1"),
        )
        result = suffix_dep(dep, "0.12.410")
        self.assertEqual(result.ref.value, "1.3.1-nanvix-0.12.410")

    def test_skips_non_version_ref(self) -> None:
        """TAG ref is returned unchanged."""
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.3.1")
        )
        result = suffix_dep(dep, "0.12.410")
        self.assertEqual(result.ref.value, "v1.3.1")

    def test_skips_already_suffixed(self) -> None:
        """VERSION ref that already contains -nanvix- is not double-suffixed."""
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.VERSION, value="1.3.1-nanvix-99.99.99"),
        )
        result = suffix_dep(dep, "0.12.410")
        self.assertEqual(result.ref.value, "1.3.1-nanvix-99.99.99")


class TestExtractNanvixVersion(unittest.TestCase):
    """Tests for extract_nanvix_version()."""

    def test_extracts_version(self) -> None:
        self.assertEqual(extract_nanvix_version("1.3.1-nanvix-0.12.291"), "0.12.291")

    def test_returns_none_without_marker(self) -> None:
        self.assertIsNone(extract_nanvix_version("v1.3.1"))

    def test_extracts_from_complex_base(self) -> None:
        self.assertEqual(extract_nanvix_version("3.12.3-nanvix-0.12.291"), "0.12.291")


class TestExtractNanvixVersionBase(unittest.TestCase):
    """Tests for extract_nanvix_version_base()."""

    def test_extracts_base(self) -> None:
        self.assertEqual(extract_nanvix_version_base("1.3.1-nanvix-0.12.291"), "1.3.1")

    def test_returns_none_without_marker(self) -> None:
        self.assertIsNone(extract_nanvix_version_base("v1.3.1"))


class TestParseSemverTuple(unittest.TestCase):
    """Tests for parse_semver_tuple()."""

    def test_parses_semver(self) -> None:
        self.assertEqual(parse_semver_tuple("0.12.291"), (0, 12, 291))

    def test_ordering(self) -> None:
        self.assertLess(parse_semver_tuple("0.12.291"), parse_semver_tuple("0.12.337"))


class TestInstallDepPreResolvedRelease(unittest.TestCase):
    """Buildroot.install_dep with _release pre-resolved."""

    def _setup_buildroot(self) -> Sysroot:
        return Sysroot(sysroot())

    def _make_archive(self) -> bytes:
        return _make_tar_bz2(
            {
                "lib/libz.a": b"lib-content",
                "include/zlib.h": b"header-content",
            }
        )

    def test_pre_resolved_release_passed_through(self) -> None:
        """When _release is provided, it's forwarded to download_release_asset."""
        br = self._setup_buildroot()
        archive = self._make_archive()
        archive_path = Path.cwd() / "zlib.tar.bz2"
        archive_path.write_bytes(archive)

        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
        )

        fake_release: dict[str, object] = {"id": 42, "tag_name": "v1.0.0"}
        captured_releases: list[dict[str, object] | None] = []

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
            allow_missing: bool = False,
        ) -> Path:
            captured_releases.append(_release)
            return archive_path

        with patch(
            "nanvix_zutil.sysroot.github.download_release_asset",
            side_effect=fake_download,
        ):
            br.install_dep(dep, _release=fake_release)

        self.assertEqual(captured_releases[0], fake_release)


class TestRefKindLocal(unittest.TestCase):
    """RefKind.LOCAL exists and round-trips correctly."""

    def test_local_variant_exists(self) -> None:
        self.assertEqual(RefKind.LOCAL.value, "local")

    def test_local_ref_round_trip(self) -> None:
        ref = Ref(kind=RefKind.LOCAL, value="/home/me/zlib-build")
        self.assertEqual(ref.kind, RefKind.LOCAL)
        self.assertEqual(ref.value, "/home/me/zlib-build")

    def test_local_ref_windows_path(self) -> None:
        ref = Ref(kind=RefKind.LOCAL, value="C:\\Users\\me\\build")
        self.assertEqual(ref.kind, RefKind.LOCAL)
        self.assertEqual(ref.value, "C:\\Users\\me\\build")

    def test_suffix_dep_skips_local(self) -> None:
        """suffix_dep returns LOCAL refs unchanged."""
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.LOCAL, value="/tmp/zlib"),
        )
        result = suffix_dep(dep, "0.12.410")
        self.assertEqual(result.ref.kind, RefKind.LOCAL)
        self.assertEqual(result.ref.value, "/tmp/zlib")


class TestBuildrootInstallDepZip(unittest.TestCase):
    """Buildroot.install_dep() extracts libs and headers from .zip archives."""

    def _setup_buildroot(self) -> Sysroot:
        return Sysroot(sysroot())

    def test_install_dep_zip_extracts_lib(self) -> None:
        br = self._setup_buildroot()
        archive = _make_zip(
            {
                "lib/libz.a": b"lib-content",
                "include/zlib.h": b"header-content",
            }
        )
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        archive_path = Path.cwd() / "zlib.zip"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((sysroot() / "lib" / "libz.a").exists())
        self.assertEqual((sysroot() / "lib" / "libz.a").read_bytes(), b"lib-content")

    def test_install_dep_zip_extracts_header(self) -> None:
        br = self._setup_buildroot()
        archive = _make_zip(
            {
                "lib/libz.a": b"lib-content",
                "include/zlib.h": b"header-content",
            }
        )
        dep = Dependency(
            name="zlib", repo="nanvix/zlib", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )
        archive_path = Path.cwd() / "zlib.zip"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((sysroot() / "include" / "zlib.h").exists())
        self.assertEqual(
            (sysroot() / "include" / "zlib.h").read_bytes(), b"header-content"
        )

    def test_install_dep_zip_preserves_header_subdirectory(self) -> None:
        br = self._setup_buildroot()
        archive = _make_zip(
            {
                "include/openssl/ssl.h": b"ssl-header",
                "include/openssl/crypto.h": b"crypto-header",
                "lib/libssl.a": b"ssl-lib",
            }
        )
        dep = Dependency(
            name="openssl",
            repo="nanvix/openssl",
            ref=Ref(kind=RefKind.TAG, value="v3.5.0"),
        )
        archive_path = Path.cwd() / "openssl.zip"
        archive_path.write_bytes(archive)

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        self.assertTrue((sysroot() / "include" / "openssl" / "ssl.h").exists())
        self.assertTrue((sysroot() / "include" / "openssl" / "crypto.h").exists())
        self.assertTrue((sysroot() / "lib" / "libssl.a").exists())

    @unittest.skipIf(sys.platform == "win32", "no Unix mode bits on Windows")
    def test_install_dep_zip_preserves_executable_bit(self) -> None:
        """zipfile drops Unix modes on extract; install must restore them."""
        br = self._setup_buildroot()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("bin/hello")
            info.external_attr = 0o755 << ZIP_MODE_SHIFT
            zf.writestr(info, b"#!/bin/sh\n")
        archive_path = Path.cwd() / "tool.zip"
        archive_path.write_bytes(buf.getvalue())
        dep = Dependency(
            name="tool", repo="nanvix/tool", ref=Ref(kind=RefKind.TAG, value="v1.0.0")
        )

        with patch(
            "nanvix_zutil.github.download_release_asset",
            return_value=archive_path,
        ):
            br.install_dep(dep)

        mode = (sysroot() / "bin" / "hello").stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
