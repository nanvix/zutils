# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.lockfile."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.buildroot import Ref, RefKind
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedAsset,
    ResolvedPackage,
    compute_manifest_hash,
    download_lockfile_asset,
    read_lockfile,
    write_lockfile,
)
from nanvix_zutil.manifest import BuildMatrix

_DEFAULT_BUILDS = BuildMatrix(
    dimensions={
        "platforms": ["hyperlight"],
        "modes": ["multi-process"],
        "memory": ["128mb"],
    },
    exclude=[],
)


def _make_sample_lockfile() -> Lockfile:
    """Create a sample lockfile for testing."""
    return Lockfile(
        metadata=LockfileMetadata(
            manifest_hash="sha256:abc123",
            nanvix_zutil_version="0.2.2",
        ),
        builds=_DEFAULT_BUILDS,
        packages=[
            ResolvedPackage(
                name="nanvix",
                repo="nanvix/nanvix",
                kind="sysroot",
                ref=Ref(kind=RefKind.TAG, value="0.12.257"),
                resolved_tag="v0.12.257",
                resolved_commitish="fa06b88abcdef1234567890abcdef1234567890ab",
                release_id=12345,
                dependencies=[],
                assets=[
                    ResolvedAsset(
                        name="nanvix-x86-hyperlight-multi-process-release-128mb-fa06b88.tar.bz2",
                        url="https://github.com/nanvix/nanvix/releases/download/v0.12.257/nanvix-x86-hyperlight-multi-process-release-128mb-fa06b88.tar.bz2",
                    ),
                ],
            ),
            ResolvedPackage(
                name="zlib",
                repo="nanvix/zlib",
                kind="dependency",
                ref=Ref(kind=RefKind.COMMITISH, value="25e1341"),
                resolved_tag="zlib-1.2.11-nanvix-fa06b88",
                resolved_commitish="25e1341abcdef1234567890abcdef1234567890ab",
                release_id=67890,
                dependencies=[],
                assets=[
                    ResolvedAsset(
                        name="zlib-hyperlight-multi-process-128mb.tar.bz2",
                        url="https://github.com/nanvix/zlib/releases/download/zlib-1.2.11-nanvix-fa06b88/zlib-hyperlight-multi-process-128mb.tar.bz2",
                    ),
                ],
                transitive=True,
                required_by=["libfoo"],
            ),
        ],
    )


class TestLockfileRoundTrip(unittest.TestCase):
    """write_lockfile → read_lockfile produces an identical Lockfile."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_round_trip(self) -> None:
        lockfile = _make_sample_lockfile()
        path = Path(self._tmpdir.name) / "nanvix.lock"

        write_lockfile(lockfile, path)
        restored = read_lockfile(path)

        # Metadata
        self.assertEqual(
            restored.metadata.manifest_hash, lockfile.metadata.manifest_hash
        )
        self.assertEqual(
            restored.metadata.nanvix_zutil_version,
            lockfile.metadata.nanvix_zutil_version,
        )

        # Packages
        self.assertEqual(len(restored.packages), len(lockfile.packages))
        for orig, rest in zip(lockfile.packages, restored.packages):
            self.assertEqual(rest.name, orig.name)
            self.assertEqual(rest.repo, orig.repo)
            self.assertEqual(rest.kind, orig.kind)
            self.assertEqual(rest.ref.kind, orig.ref.kind)
            self.assertEqual(rest.ref.value, orig.ref.value)
            self.assertEqual(rest.resolved_tag, orig.resolved_tag)
            self.assertEqual(rest.resolved_commitish, orig.resolved_commitish)
            self.assertEqual(rest.release_id, orig.release_id)
            self.assertEqual(rest.dependencies, orig.dependencies)
            self.assertEqual(rest.transitive, orig.transitive)
            self.assertEqual(rest.required_by, orig.required_by)

            # Assets
            self.assertEqual(len(rest.assets), len(orig.assets))
            for a_orig, a_rest in zip(orig.assets, rest.assets):
                self.assertEqual(a_rest.name, a_orig.name)
                self.assertEqual(a_rest.url, a_orig.url)

    def test_round_trip_no_transitive_fields(self) -> None:
        """Non-transitive packages omit transitive/required-by in TOML."""
        lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:def456",
                nanvix_zutil_version="0.2.2",
            ),
            builds=_DEFAULT_BUILDS,
            packages=[
                ResolvedPackage(
                    name="nanvix",
                    repo="nanvix/nanvix",
                    kind="sysroot",
                    ref=Ref(kind=RefKind.TAG, value="0.1.0"),
                    resolved_tag="v0.1.0",
                    resolved_commitish="aaa111",
                    release_id=1,
                ),
            ],
        )
        path = Path(self._tmpdir.name) / "nanvix.lock"
        write_lockfile(lockfile, path)

        content = path.read_text()
        self.assertNotIn("transitive", content)
        self.assertNotIn("required-by", content)

        restored = read_lockfile(path)
        self.assertFalse(restored.packages[0].transitive)
        self.assertEqual(restored.packages[0].required_by, [])

    def test_header_comment(self) -> None:
        lockfile = _make_sample_lockfile()
        path = Path(self._tmpdir.name) / "nanvix.lock"
        write_lockfile(lockfile, path)

        content = path.read_text()
        self.assertTrue(content.startswith("# nanvix.lock"))

    def test_round_trip_id_ref(self) -> None:
        """Integer ref-value (RefKind.ID) survives round-trip."""
        lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:id_test",
                nanvix_zutil_version="0.2.2",
            ),
            builds=_DEFAULT_BUILDS,
            packages=[
                ResolvedPackage(
                    name="nanvix",
                    repo="nanvix/nanvix",
                    kind="sysroot",
                    ref=Ref(kind=RefKind.ID, value=99999),
                    resolved_tag="v0.1.0",
                    resolved_commitish="aaa111",
                    release_id=1,
                ),
            ],
        )
        path = Path(self._tmpdir.name) / "nanvix.lock"
        write_lockfile(lockfile, path)
        restored = read_lockfile(path)
        self.assertEqual(restored.packages[0].ref.kind, RefKind.ID)
        self.assertEqual(restored.packages[0].ref.value, 99999)
        self.assertIsInstance(restored.packages[0].ref.value, int)

    def test_round_trip_local_ref(self) -> None:
        """String ref-value (RefKind.LOCAL) survives round-trip."""
        lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:local_test",
                nanvix_zutil_version="0.2.2",
            ),
            builds=_DEFAULT_BUILDS,
            packages=[
                ResolvedPackage(
                    name="nanvix",
                    repo="nanvix/nanvix",
                    kind="sysroot",
                    ref=Ref(kind=RefKind.LOCAL, value="/opt/nanvix/sysroot"),
                    resolved_tag="",
                    resolved_commitish="",
                    release_id=0,
                ),
            ],
        )
        path = Path(self._tmpdir.name) / "nanvix.lock"
        write_lockfile(lockfile, path)
        restored = read_lockfile(path)
        self.assertEqual(restored.packages[0].ref.kind, RefKind.LOCAL)
        self.assertEqual(restored.packages[0].ref.value, "/opt/nanvix/sysroot")
        self.assertIsInstance(restored.packages[0].ref.value, str)


class TestComputeManifestHash(unittest.TestCase):
    """compute_manifest_hash tests."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_deterministic(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text('[package]\nname = "test"\nversion = "0.1.0"\n')

        h1 = compute_manifest_hash(path)
        h2 = compute_manifest_hash(path)
        self.assertEqual(h1, h2)
        self.assertTrue(h1.startswith("sha256:"))

    def test_changes_with_content(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"

        path.write_text("content-a")
        h1 = compute_manifest_hash(path)

        path.write_text("content-b")
        h2 = compute_manifest_hash(path)

        self.assertNotEqual(h1, h2)


class TestReadLockfileMalformed(unittest.TestCase):
    """read_lockfile exits with correct code on malformed TOML."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_missing_file_exits_3(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.lock"
        with self.assertRaises(SystemExit) as ctx:
            read_lockfile(path)
        self.assertEqual(ctx.exception.code, 3)

    def test_malformed_toml_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.lock"
        path.write_text("[[invalid toml =")
        with self.assertRaises(SystemExit) as ctx:
            read_lockfile(path)
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_metadata_exits_2(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.lock"
        path.write_text("[[package]]\nname = 'x'\n")
        with self.assertRaises(SystemExit) as ctx:
            read_lockfile(path)
        self.assertEqual(ctx.exception.code, 2)


class TestDownloadLockfileAsset(unittest.TestCase):
    """download_lockfile_asset tests."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_returns_none_when_no_lockfile_asset(self) -> None:
        release: dict[str, object] = {
            "assets": [
                {
                    "name": "zlib-hyperlight-multi-process-128mb.tar.bz2",
                    "browser_download_url": "https://example.com/foo.tar.bz2",
                }
            ]
        }
        result = download_lockfile_asset(release, Path(self._tmpdir.name))
        self.assertIsNone(result)

    def test_returns_none_when_no_assets(self) -> None:
        release: dict[str, object] = {"assets": []}
        result = download_lockfile_asset(release, Path(self._tmpdir.name))
        self.assertIsNone(result)

    @patch("nanvix_zutil.lockfile.github.download_release_asset")
    def test_downloads_and_parses_lockfile(self, mock_download: MagicMock) -> None:
        # Build a minimal valid lockfile and write it to a temp file
        # that the mock will return as the download path.
        lockfile_content = (
            "[metadata]\n"
            'manifest-hash = "sha256:abc"\n'
            'nanvix-zutil-version = "0.2.2"\n'
            "\n"
            "[builds]\n"
            "[builds.matrix]\n"
            'platforms = ["hyperlight"]\n'
            'modes = ["multi-process"]\n'
            'memory = ["128mb"]\n'
            "\n"
            "[[package]]\n"
            'name = "nanvix"\n'
            'repo = "nanvix/nanvix"\n'
            'kind = "sysroot"\n'
            'ref-kind = "tag"\n'
            'ref-value = "0.1.0"\n'
            'resolved-tag = "v0.1.0"\n'
            'resolved-commitish = "aaa"\n'
            "release-id = 1\n"
            "dependencies = []\n"
        )
        out_path = Path(self._tmpdir.name) / "nanvix.lock"
        out_path.write_text(lockfile_content)
        mock_download.return_value = out_path

        release: dict[str, object] = {
            "assets": [
                {
                    "name": "nanvix.lock",
                    "browser_download_url": "https://example.com/nanvix.lock",
                }
            ]
        }

        result = download_lockfile_asset(release, Path(self._tmpdir.name))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata.manifest_hash, "sha256:abc")
        self.assertEqual(len(result.packages), 1)
        self.assertEqual(result.packages[0].name, "nanvix")


class TestRoundTripWithBuilds(unittest.TestCase):
    """Lockfile round-trip preserves the [builds] section."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_roundtrip_with_builds(self) -> None:
        """write_lockfile → read_lockfile preserves builds.dimensions and builds.exclude."""
        builds = BuildMatrix(
            dimensions={
                "platforms": ["hyperlight", "microvm"],
                "modes": ["multi-process", "standalone"],
                "memory": ["128mb", "256mb"],
            },
            exclude=[
                {"platform": "hyperlight", "mode": "standalone"},
            ],
        )
        lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:builds_test",
                nanvix_zutil_version="0.2.2",
            ),
            builds=builds,
        )
        path = Path(self._tmpdir.name) / "nanvix.lock"

        write_lockfile(lockfile, path)
        restored = read_lockfile(path)

        self.assertEqual(
            restored.builds.dimensions["platforms"],
            builds.dimensions["platforms"],
        )
        self.assertEqual(
            restored.builds.dimensions["modes"],
            builds.dimensions["modes"],
        )
        self.assertEqual(
            restored.builds.dimensions["memory"],
            builds.dimensions["memory"],
        )
        self.assertEqual(len(restored.builds.exclude), 1)
        self.assertEqual(restored.builds.exclude[0]["platform"], "hyperlight")
        self.assertEqual(restored.builds.exclude[0]["mode"], "standalone")


if __name__ == "__main__":
    unittest.main()
