# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# pyright: reportPrivateUsage=false

"""Tests for nanvix_zutil.resolver."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
from nanvix_zutil.buildroot import Dependency, Ref, RefKind
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedPackage,
)
from nanvix_zutil.manifest import Manifest
from nanvix_zutil.resolver import _unsuffix_deps, is_stale, resolve


def _make_release(
    tag: str = "v0.1.0",
    commitish: str = "aaa111",
    release_id: int = 100,
    assets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build a fake GitHub release dict."""
    if assets is None:
        assets = []
    return {
        "tag_name": tag,
        "target_commitish": commitish,
        "id": release_id,
        "assets": assets,
    }


def _make_manifest(
    deps: list[Dependency] | None = None,
    sys_deps: list[Dependency] | None = None,
    sysroot_ref: Ref | None = None,
) -> Manifest:
    """Build a Manifest with the given dependencies."""
    return Manifest(
        name="test",
        version="0.1.0",
        sysroot_ref=sysroot_ref or Ref(kind=RefKind.TAG, value="0.1.0"),
        dependencies=deps or [],
        system_dependencies=sys_deps or [],
    )


def _archive_asset(name: str) -> dict[str, object]:
    """Build a fake archive asset entry."""
    return {
        "name": name,
        "browser_download_url": f"https://example.com/{name}",
    }


def _lockfile_asset() -> dict[str, object]:
    """Build a fake nanvix.lock asset entry."""
    return {
        "name": "nanvix.lock",
        "browser_download_url": "https://example.com/nanvix.lock",
    }


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestResolveSysrootOnly(unittest.TestCase):
    """Resolver with no dependencies (sysroot only)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\nnanvix-version = "0.1.0"\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_sysroot_only(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        sysroot_release = _make_release(
            tag="v0.1.0",
            commitish="aaa111",
            release_id=100,
            assets=[
                _archive_asset(
                    "nanvix-hyperlight-multi-process-release-128mb-aaa111.tar.bz2"
                )
            ],
        )
        mock_resolve.return_value = sysroot_release
        mock_download.return_value = None

        manifest = _make_manifest()
        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

        self.assertEqual(len(lockfile.packages), 1)
        self.assertEqual(lockfile.packages[0].name, "nanvix")
        self.assertEqual(lockfile.packages[0].kind, "sysroot")
        self.assertEqual(len(lockfile.packages[0].assets), 1)
        self.assertTrue(lockfile.metadata.manifest_hash.startswith("sha256:"))


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestResolveDirectDep(unittest.TestCase):
    """Resolver with one direct dependency."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "0.1.0"\n'
            "[dependencies]\n"
            'zlib = { tag = "v1.0.0" }\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_one_direct_dep(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        sysroot_release = _make_release(tag="v0.1.0", release_id=100)
        zlib_release = _make_release(
            tag="v1.0.0",
            commitish="bbb222",
            release_id=200,
            assets=[_archive_asset("zlib-hyperlight-multi-process-128mb.tar.bz2")],
        )
        mock_resolve.side_effect = [sysroot_release, zlib_release]
        mock_download.return_value = None

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="zlib",
                    repo="nanvix/zlib",
                    ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
                )
            ]
        )
        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

        self.assertEqual(len(lockfile.packages), 2)
        names = [p.name for p in lockfile.packages]
        self.assertIn("nanvix", names)
        self.assertIn("zlib", names)

        zlib_pkg = next(p for p in lockfile.packages if p.name == "zlib")
        self.assertEqual(zlib_pkg.kind, "dependency")
        self.assertFalse(zlib_pkg.transitive)
        self.assertEqual(len(zlib_pkg.assets), 1)


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestResolveTransitive(unittest.TestCase):
    """Resolver discovers transitive deps from a dep's lockfile asset."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "0.1.0"\n'
            "[dependencies]\n"
            'libfoo = { tag = "v1.0.0" }\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_transitive_discovery(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        sysroot_release = _make_release(tag="v0.1.0", release_id=100)
        libfoo_release = _make_release(
            tag="v1.0.0",
            commitish="ccc333",
            release_id=300,
            assets=[
                _archive_asset("libfoo-hyperlight-multi-process-128mb.tar.bz2"),
                _lockfile_asset(),
            ],
        )
        zlib_release = _make_release(
            tag="v2.0.0",
            commitish="ddd444",
            release_id=400,
            assets=[_archive_asset("zlib-hyperlight-multi-process-128mb.tar.bz2")],
        )

        mock_resolve.side_effect = [sysroot_release, libfoo_release, zlib_release]

        # libfoo's lockfile lists zlib as a dependency
        inner_lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:inner",
                nanvix_zutil_version="0.2.2",
            ),
            packages=[
                ResolvedPackage(
                    name="zlib",
                    repo="nanvix/zlib",
                    kind="dependency",
                    ref=Ref(kind=RefKind.TAG, value="v2.0.0"),
                    resolved_tag="v2.0.0",
                    resolved_commitish="ddd444",
                    release_id=400,
                ),
            ],
        )
        # First call (for libfoo) returns inner lockfile,
        # second call (for zlib) returns None (leaf)
        mock_download.side_effect = [inner_lockfile, None]

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="libfoo",
                    repo="nanvix/libfoo",
                    ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
                )
            ]
        )
        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

        self.assertEqual(len(lockfile.packages), 3)
        names = [p.name for p in lockfile.packages]
        self.assertIn("zlib", names)

        zlib_pkg = next(p for p in lockfile.packages if p.name == "zlib")
        self.assertTrue(zlib_pkg.transitive)
        self.assertIn("libfoo", zlib_pkg.required_by)

        libfoo_pkg = next(p for p in lockfile.packages if p.name == "libfoo")
        self.assertIn("zlib", libfoo_pkg.dependencies)


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestResolveShallow(unittest.TestCase):
    """shallow=True does not download lockfile assets."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "0.1.0"\n'
            "[dependencies]\n"
            'libfoo = { tag = "v1.0.0" }\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_shallow_skips_transitive(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        sysroot_release = _make_release(tag="v0.1.0", release_id=100)
        libfoo_release = _make_release(tag="v1.0.0", commitish="ccc333", release_id=300)
        mock_resolve.side_effect = [sysroot_release, libfoo_release]

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="libfoo",
                    repo="nanvix/libfoo",
                    ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
                )
            ]
        )
        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            shallow=True,
            manifest_path=self._manifest_path,
        )

        # download_lockfile_asset should NOT be called
        mock_download.assert_not_called()
        # Only sysroot + direct dep
        self.assertEqual(len(lockfile.packages), 2)


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestCycleDetection(unittest.TestCase):
    """Resolver detects and rejects dependency cycles."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "0.1.0"\n'
            "[dependencies]\n"
            'liba = { tag = "v1.0.0" }\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_cycle_detected(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        sysroot_release = _make_release(tag="v0.1.0", release_id=100)
        liba_release = _make_release(tag="v1.0.0", commitish="aaa", release_id=200)
        libb_release = _make_release(tag="v1.0.0", commitish="bbb", release_id=300)

        mock_resolve.side_effect = [sysroot_release, liba_release, libb_release]

        # liba depends on libb
        liba_lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:a", nanvix_zutil_version="0.2.2"
            ),
            packages=[
                ResolvedPackage(
                    name="libb",
                    repo="nanvix/libb",
                    kind="dependency",
                    ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
                    resolved_tag="v1.0.0",
                    resolved_commitish="bbb",
                    release_id=300,
                ),
            ],
        )
        # libb depends on liba → cycle
        libb_lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:b", nanvix_zutil_version="0.2.2"
            ),
            packages=[
                ResolvedPackage(
                    name="liba",
                    repo="nanvix/liba",
                    kind="dependency",
                    ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
                    resolved_tag="v1.0.0",
                    resolved_commitish="aaa",
                    release_id=200,
                ),
            ],
        )
        mock_download.side_effect = [liba_lockfile, libb_lockfile]

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="liba",
                    repo="nanvix/liba",
                    ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
                )
            ]
        )

        with self.assertRaises(SystemExit) as ctx:
            resolve(
                manifest,
                cache_dir=Path(self._tmpdir.name) / "cache",
                manifest_path=self._manifest_path,
            )
        self.assertEqual(ctx.exception.code, 2)


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestVersionConflict(unittest.TestCase):
    """Resolver detects version conflicts."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "0.1.0"\n'
            "[dependencies]\n"
            'libfoo = { tag = "v1.0.0" }\n'
            'zlib = { tag = "v1.0.0" }\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_conflict_exits_2(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        sysroot_release = _make_release(tag="v0.1.0", release_id=100)
        zlib_release = _make_release(tag="v1.0.0", commitish="aaa", release_id=200)
        libfoo_release = _make_release(tag="v1.0.0", commitish="bbb", release_id=300)

        mock_resolve.side_effect = [
            sysroot_release,
            zlib_release,
            libfoo_release,
        ]

        # libfoo's lockfile lists zlib at a DIFFERENT version
        inner_lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:inner", nanvix_zutil_version="0.2.2"
            ),
            packages=[
                ResolvedPackage(
                    name="zlib",
                    repo="nanvix/zlib",
                    kind="dependency",
                    ref=Ref(kind=RefKind.TAG, value="v2.0.0"),
                    resolved_tag="v2.0.0",
                    resolved_commitish="ccc",
                    release_id=999,
                ),
            ],
        )
        mock_download.side_effect = [None, inner_lockfile]

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="zlib",
                    repo="nanvix/zlib",
                    ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
                ),
                Dependency(
                    name="libfoo",
                    repo="nanvix/libfoo",
                    ref=Ref(kind=RefKind.TAG, value="v1.0.0"),
                ),
            ]
        )

        with self.assertRaises(SystemExit) as ctx:
            resolve(
                manifest,
                cache_dir=Path(self._tmpdir.name) / "cache",
                manifest_path=self._manifest_path,
            )
        self.assertEqual(ctx.exception.code, 2)


class TestIsStale(unittest.TestCase):
    """is_stale() returns correct results."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_not_stale(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text('[package]\nname = "test"\n')

        from nanvix_zutil.lockfile import compute_manifest_hash

        h = compute_manifest_hash(path)
        lockfile = Lockfile(
            metadata=LockfileMetadata(manifest_hash=h, nanvix_zutil_version="0.2.2"),
        )
        self.assertFalse(is_stale(lockfile, path))

    def test_stale(self) -> None:
        path = Path(self._tmpdir.name) / "nanvix.toml"
        path.write_text('[package]\nname = "test"\n')

        lockfile = Lockfile(
            metadata=LockfileMetadata(
                manifest_hash="sha256:old", nanvix_zutil_version="0.2.2"
            ),
        )
        self.assertTrue(is_stale(lockfile, path))


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
@patch("nanvix_zutil.resolver.github.resolve_release_with_fallback")
class TestResolveLatestSysroot(unittest.TestCase):
    """Resolver with 'latest' sysroot and a VERSION dep."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "latest"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_latest_sysroot_deferred_suffix(
        self,
        mock_fallback: MagicMock,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        sysroot_release = _make_release(
            tag="v0.12.277", commitish="fa06b88", release_id=100
        )
        zlib_release = _make_release(
            tag="v1.3.1-nanvix-0.12.277",
            commitish="bbb222",
            release_id=200,
            assets=[_archive_asset("zlib-hyperlight-multi-process-128mb.tar.bz2")],
        )
        # resolve_release is only called for sysroot — zlib is served
        # from the probe cache, eliminating a duplicate API call.
        mock_resolve.side_effect = [sysroot_release]
        # Fallback probe: exact tag found, no fallback needed.
        mock_fallback.return_value = (zlib_release, None)
        mock_download.return_value = None

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="zlib",
                    repo="nanvix/zlib",
                    ref=Ref(kind=RefKind.VERSION, value="1.3.1"),
                )
            ],
            sysroot_ref=Ref(kind=RefKind.TAG, value="latest"),
        )

        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

        self.assertEqual(len(lockfile.packages), 2)
        # Verify sysroot resolved correctly
        sysroot_pkg = next(p for p in lockfile.packages if p.name == "nanvix")
        self.assertEqual(sysroot_pkg.resolved_tag, "v0.12.277")
        # Verify dep was suffixed with resolved version
        zlib_pkg = next(p for p in lockfile.packages if p.name == "zlib")
        self.assertEqual(zlib_pkg.ref.value, "1.3.1-nanvix-0.12.277")
        # zlib resolved via probe cache — resolve_release NOT called for it.
        mock_resolve.assert_called_once_with(
            "nanvix/nanvix", "latest", gh_token=None, semver=True
        )
        # Probe was called with the suffixed tag.
        mock_fallback.assert_called_once_with(
            "nanvix/zlib",
            "1.3.1-nanvix-0.12.277",
            "1.3.1",
            gh_token=None,
        )


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestAssetFiltering(unittest.TestCase):
    """Resolver filters out non-archive assets and nanvix.lock."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\nnanvix-version = "0.1.0"\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_filters_non_tar_bz2(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        sysroot_release = _make_release(
            tag="v0.1.0",
            release_id=100,
            assets=[
                _archive_asset("nanvix-hyperlight-multi-process-release-128mb.tar.bz2"),
                _archive_asset("nanvix-hyperlight-multi-process-release-128mb.tar.gz"),
                _archive_asset("nanvix-hyperlight-multi-process-release-128mb.zip"),
                _lockfile_asset(),
                {
                    "name": "notes.txt",
                    "browser_download_url": "https://example.com/notes.txt",
                },
            ],
        )
        mock_resolve.return_value = sysroot_release
        mock_download.return_value = None

        manifest = _make_manifest()
        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

        sysroot_pkg = lockfile.packages[0]
        # .tar.bz2, .tar.gz, and .zip are collected; .txt and nanvix.lock are not.
        self.assertEqual(len(sysroot_pkg.assets), 3)
        names = {a.name for a in sysroot_pkg.assets}
        self.assertIn("nanvix-hyperlight-multi-process-release-128mb.tar.bz2", names)
        self.assertIn("nanvix-hyperlight-multi-process-release-128mb.tar.gz", names)
        self.assertIn("nanvix-hyperlight-multi-process-release-128mb.zip", names)


class TestUnsuffixDeps(unittest.TestCase):
    """Tests for _unsuffix_deps()."""

    def test_strips_suffix(self) -> None:
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.VERSION, value="1.3.1-nanvix-0.12.337"),
        )
        result = _unsuffix_deps([dep])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ref.value, "1.3.1")

    def test_preserves_non_version_refs(self) -> None:
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.TAG, value="v1.3.1-nanvix-0.12.337"),
        )
        result = _unsuffix_deps([dep])
        self.assertEqual(result[0].ref.value, "v1.3.1-nanvix-0.12.337")

    def test_preserves_unsuffixed(self) -> None:
        dep = Dependency(
            name="zlib",
            repo="nanvix/zlib",
            ref=Ref(kind=RefKind.VERSION, value="1.3.1"),
        )
        result = _unsuffix_deps([dep])
        self.assertEqual(result[0].ref.value, "1.3.1")


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
@patch("nanvix_zutil.resolver.github.resolve_release_with_fallback")
class TestResolveVersionFallback(unittest.TestCase):
    """Tests for fallback version downgrade in the resolver."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "latest"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_fallback_downgrades_sysroot(
        self,
        mock_fallback: MagicMock,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        """Sysroot is downgraded when dep 404s and fallback finds older version."""
        latest_sysroot = _make_release(
            tag="v0.12.337", commitish="aaa111", release_id=100
        )
        downgraded_sysroot = _make_release(
            tag="v0.12.291", commitish="bbb222", release_id=101
        )
        zlib_release = _make_release(
            tag="v1.3.1-nanvix-0.12.291",
            commitish="ccc333",
            release_id=200,
            assets=[_archive_asset("zlib-hyperlight-multi-process-128mb.tar.bz2")],
        )
        # resolve_release: (1) sysroot@latest, (2) sysroot@0.12.291, (3) zlib
        mock_resolve.side_effect = [latest_sysroot, downgraded_sysroot, zlib_release]
        # Fallback probe: zlib 404 → fallback to 0.12.291
        mock_fallback.return_value = (zlib_release, "0.12.291")
        mock_download.return_value = None

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="zlib",
                    repo="nanvix/zlib",
                    ref=Ref(kind=RefKind.VERSION, value="1.3.1"),
                )
            ],
            sysroot_ref=Ref(kind=RefKind.TAG, value="latest"),
        )

        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

        # Sysroot should be downgraded to 0.12.291
        sysroot_pkg = next(p for p in lockfile.packages if p.name == "nanvix")
        self.assertEqual(sysroot_pkg.resolved_tag, "v0.12.291")
        # zlib should use the downgraded suffix
        zlib_pkg = next(p for p in lockfile.packages if p.name == "zlib")
        self.assertEqual(zlib_pkg.ref.value, "1.3.1-nanvix-0.12.291")

    def test_fallback_uses_minimum_across_deps(
        self,
        mock_fallback: MagicMock,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        """When multiple deps fall back, sysroot uses the minimum version."""
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "latest"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
            'sqlite = "3.49.0"\n'
        )
        latest_sysroot = _make_release(
            tag="v0.12.337", commitish="aaa111", release_id=100
        )
        downgraded_sysroot = _make_release(
            tag="v0.12.291", commitish="bbb222", release_id=101
        )
        zlib_release = _make_release(
            tag="v1.3.1-nanvix-0.12.291",
            commitish="ccc333",
            release_id=200,
        )
        sqlite_release = _make_release(
            tag="v3.49.0-nanvix-0.12.291",
            commitish="ddd444",
            release_id=300,
        )
        # resolve_release: (1) sysroot@latest, (2) sysroot@0.12.291,
        # (3) zlib, (4) sqlite
        mock_resolve.side_effect = [
            latest_sysroot,
            downgraded_sysroot,
            zlib_release,
            sqlite_release,
        ]
        # Fallback probe: zlib → 0.12.291, sqlite → 0.12.320
        mock_fallback.side_effect = [
            (zlib_release, "0.12.291"),
            (sqlite_release, "0.12.320"),
        ]
        mock_download.return_value = None

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="zlib",
                    repo="nanvix/zlib",
                    ref=Ref(kind=RefKind.VERSION, value="1.3.1"),
                ),
                Dependency(
                    name="sqlite",
                    repo="nanvix/sqlite",
                    ref=Ref(kind=RefKind.VERSION, value="3.49.0"),
                ),
            ],
            sysroot_ref=Ref(kind=RefKind.TAG, value="latest"),
        )

        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

        # Sysroot should use the minimum: 0.12.291
        sysroot_pkg = next(p for p in lockfile.packages if p.name == "nanvix")
        self.assertEqual(sysroot_pkg.resolved_tag, "v0.12.291")

    def test_no_fallback_when_all_deps_available(
        self,
        mock_fallback: MagicMock,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        """No warning when all deps resolve on first try."""
        sysroot_release = _make_release(
            tag="v0.12.337", commitish="aaa111", release_id=100
        )
        zlib_release = _make_release(
            tag="v1.3.1-nanvix-0.12.337",
            commitish="ccc333",
            release_id=200,
            assets=[_archive_asset("zlib-hyperlight-multi-process-128mb.tar.bz2")],
        )
        # resolve_release only called for sysroot — zlib served from
        # probe cache.
        mock_resolve.side_effect = [sysroot_release]
        # Fallback probe: exact tag found, no fallback.
        mock_fallback.return_value = (zlib_release, None)
        mock_download.return_value = None

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="zlib",
                    repo="nanvix/zlib",
                    ref=Ref(kind=RefKind.VERSION, value="1.3.1"),
                )
            ],
            sysroot_ref=Ref(kind=RefKind.TAG, value="latest"),
        )

        lockfile = resolve(
            manifest,
            cache_dir=Path(self._tmpdir.name) / "cache",
            manifest_path=self._manifest_path,
        )

        sysroot_pkg = next(p for p in lockfile.packages if p.name == "nanvix")
        self.assertEqual(sysroot_pkg.resolved_tag, "v0.12.337")


@patch("nanvix_zutil.resolver.download_lockfile_asset")
@patch("nanvix_zutil.resolver.github.resolve_release")
class TestNoFallbackWhenPinned(unittest.TestCase):
    """Pinned sysroot preserves hard-fail behavior."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._manifest_dir = Path(self._tmpdir.name) / ".nanvix"
        self._manifest_dir.mkdir(parents=True)
        self._manifest_path = self._manifest_dir / "nanvix.toml"
        self._manifest_path.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n'
            'nanvix-version = "0.12.337"\n'
            "[dependencies]\n"
            'zlib = "1.3.1"\n'
        )
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_no_fallback_when_pinned(
        self,
        mock_resolve: MagicMock,
        mock_download: MagicMock,
    ) -> None:
        """Pinned version hard-fails on 404 (no fallback)."""
        sysroot_release = _make_release(
            tag="v0.12.337", commitish="aaa111", release_id=100
        )
        mock_resolve.side_effect = [sysroot_release, SystemExit(3)]
        mock_download.return_value = None

        manifest = _make_manifest(
            deps=[
                Dependency(
                    name="zlib",
                    repo="nanvix/zlib",
                    ref=Ref(
                        kind=RefKind.VERSION,
                        value="1.3.1-nanvix-0.12.337",
                    ),
                )
            ],
            sysroot_ref=Ref(kind=RefKind.TAG, value="0.12.337"),
        )

        with self.assertRaises(SystemExit) as ctx:
            resolve(
                manifest,
                cache_dir=Path(self._tmpdir.name) / "cache",
                manifest_path=self._manifest_path,
            )
        self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
