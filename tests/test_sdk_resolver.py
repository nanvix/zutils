# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Strict SDK resolver tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanvix_zutil import paths
from nanvix_zutil.buildroot import Dependency, Ref, RefKind
from nanvix_zutil.lockfile import Lockfile, LockfileMetadata, ResolvedPackage
from nanvix_zutil.manifest import Manifest, SdkPin, Toolchain, ToolchainKind
from nanvix_zutil.resolver import BlockedResolution, resolve
from nanvix_zutil.sdk import validate_sdk_release
from tests.test_sdk import make_sdk_contract


def release(tag: str, commit: str, release_id: int) -> dict[str, object]:
    """Build minimal GitHub release metadata."""
    return {
        "tag_name": tag,
        "target_commitish": commit,
        "id": release_id,
        "assets": [],
    }


class TestStrictSdkResolver(unittest.TestCase):
    """Strict mode uses exact SDK tags and returns blocked results."""

    def setUp(self) -> None:
        self.commit_patcher = patch(
            "nanvix_zutil.resolver.github.resolve_commit",
            return_value="c" * 40,
        )
        self.commit_patcher.start()
        self.addCleanup(self.commit_patcher.stop)
        paths.manifest_path().write_text(
            "[package]\n"
            'name = "test"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.20.0"\n'
        )
        self.contract = validate_sdk_release(make_sdk_contract())
        self.manifest = Manifest(
            name="test",
            version="1.0.0",
            sysroot_ref=Ref(RefKind.TAG, "0.20.0"),
            dependencies=[
                Dependency(
                    "zlib",
                    "nanvix/zlib",
                    Ref(RefKind.VERSION, "1.3.1-nanvix-0.20.0-sdk.1"),
                )
            ],
            toolchain=Toolchain(
                ToolchainKind.SDK,
                SdkPin(
                    self.contract.sdk_version,
                    self.contract.provider_id,
                    self.contract.image.name,
                    self.contract.image.digest,
                ),
            ),
        )

    @patch("nanvix_zutil.resolver.github.resolve_release")
    @patch("nanvix_zutil.resolver.resolve_sdk_release")
    def test_missing_exact_release_is_blocked(
        self,
        mock_sdk: MagicMock,
        mock_release: MagicMock,
    ) -> None:
        mock_sdk.return_value = self.contract
        mock_release.side_effect = [
            release("v0.20.0", "c" * 40, 1),
            SystemExit(3),
        ]
        result = resolve(
            self.manifest,
            cache_dir=Path.cwd() / "cache",
        )
        self.assertIsInstance(result, BlockedResolution)
        assert isinstance(result, BlockedResolution)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "exact-sdk-release-missing")
        self.assertEqual(result.requested_tag, "1.3.1-nanvix-0.20.0-sdk.1")

    @patch("nanvix_zutil.resolver.github.resolve_release")
    @patch("nanvix_zutil.resolver.resolve_sdk_release")
    @patch("nanvix_zutil.resolver.download_lockfile_asset")
    def test_exact_release_has_sdk_provenance(
        self,
        mock_lock: MagicMock,
        mock_sdk: MagicMock,
        mock_release: MagicMock,
    ) -> None:
        mock_sdk.return_value = self.contract
        mock_release.side_effect = [
            release("v0.20.0", "c" * 40, 1),
            release("1.3.1-nanvix-0.20.0-sdk.1", "e" * 40, 2),
        ]
        mock_lock.return_value = Lockfile(
            LockfileMetadata(
                manifest_hash="sha256:dep",
                nanvix_zutil_version="0.15.0",
                sdk=self.contract.provenance(),
                shallow=True,
            )
        )
        result = resolve(
            self.manifest,
            cache_dir=Path.cwd() / "cache",
            shallow=True,
        )
        self.assertNotIsInstance(result, BlockedResolution)
        assert not isinstance(result, BlockedResolution)
        self.assertEqual(result.metadata.sdk, self.contract.provenance())
        self.assertTrue(result.metadata.shallow)
        self.assertEqual(
            result.packages[1].resolved_tag,
            "1.3.1-nanvix-0.20.0-sdk.1",
        )
        mock_sdk.assert_called_once_with(
            "v0.20.0-sdk.1",
            provider_id="c-clang",
            gh_token=None,
            verify_image=True,
        )

    @patch("nanvix_zutil.resolver.github.resolve_release")
    @patch("nanvix_zutil.resolver.resolve_sdk_release")
    @patch("nanvix_zutil.resolver.download_lockfile_asset")
    def test_missing_provenance_lock_is_blocked(
        self,
        mock_lock: MagicMock,
        mock_sdk: MagicMock,
        mock_release: MagicMock,
    ) -> None:
        mock_sdk.return_value = self.contract
        mock_release.side_effect = [
            release("v0.20.0", "c" * 40, 1),
            release("1.3.1-nanvix-0.20.0-sdk.1", "e" * 40, 2),
        ]
        mock_lock.return_value = None
        result = resolve(
            self.manifest,
            cache_dir=Path.cwd() / "cache",
        )
        self.assertIsInstance(result, BlockedResolution)
        assert isinstance(result, BlockedResolution)
        self.assertEqual(result.reason, "provenance-lock-missing")

    def test_manual_tag_is_rejected_in_strict_mode(self) -> None:
        self.manifest.dependencies[0].ref = Ref(RefKind.TAG, "fallback")
        with (
            patch(
                "nanvix_zutil.resolver.resolve_sdk_release",
                return_value=self.contract,
            ),
            patch(
                "nanvix_zutil.resolver.github.resolve_release",
                return_value=release("v0.20.0", "c" * 40, 1),
            ),
        ):
            with self.assertRaises(SystemExit) as context:
                resolve(
                    self.manifest,
                    cache_dir=Path.cwd() / "cache",
                )
        self.assertEqual(context.exception.code, 2)

    def test_runtime_tag_commit_skew_is_rejected(self) -> None:
        with (
            patch(
                "nanvix_zutil.resolver.resolve_sdk_release",
                return_value=self.contract,
            ),
            patch(
                "nanvix_zutil.resolver.github.resolve_release",
                return_value=release("v0.20.0", "dev", 1),
            ),
            patch(
                "nanvix_zutil.resolver.github.resolve_commit",
                return_value="d" * 40,
            ),
        ):
            with self.assertRaises(SystemExit) as context:
                resolve(
                    self.manifest,
                    cache_dir=Path.cwd() / "cache",
                )
        self.assertEqual(context.exception.code, 2)

    def test_non_sdk_transitive_coordinate_is_rejected(self) -> None:
        inner = Lockfile(
            LockfileMetadata(
                manifest_hash="sha256:dep",
                nanvix_zutil_version="0.15.0",
                sdk=self.contract.provenance(),
                shallow=True,
            ),
            packages=[
                ResolvedPackage(
                    name="bzip2",
                    repo="nanvix/bzip2",
                    kind="dependency",
                    ref=Ref(RefKind.TAG, "legacy-tag"),
                    resolved_tag="legacy-tag",
                    resolved_commitish="f" * 40,
                    release_id=3,
                )
            ],
        )
        with (
            patch(
                "nanvix_zutil.resolver.resolve_sdk_release",
                return_value=self.contract,
            ),
            patch(
                "nanvix_zutil.resolver.github.resolve_release",
                side_effect=[
                    release("v0.20.0", "c" * 40, 1),
                    release("1.3.1-nanvix-0.20.0-sdk.1", "e" * 40, 2),
                ],
            ),
            patch(
                "nanvix_zutil.resolver.download_lockfile_asset",
                return_value=inner,
            ),
        ):
            with self.assertRaises(SystemExit) as context:
                resolve(
                    self.manifest,
                    cache_dir=Path.cwd() / "cache",
                )
        self.assertEqual(context.exception.code, 2)


SDK_SUFFIX = "-nanvix-0.20.0-sdk.1"


class TestResolverLocalOverrides(unittest.TestCase):
    """``resolve(local_overrides=...)`` fatals on diamond hazards.

    A released package that depends on a locally overridden name is
    fatal unless that package is itself overridden.
    """

    def setUp(self) -> None:
        paths.manifest_path().write_text(
            "[package]\n"
            'name = "test"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.20.0"\n'
        )
        self.contract = validate_sdk_release(make_sdk_contract())
        self.commit_patcher = patch(
            "nanvix_zutil.resolver.github.resolve_commit",
            return_value="c" * 40,
        )
        self.commit_patcher.start()
        self.addCleanup(self.commit_patcher.stop)

    def _manifest(self, *dep_names: str) -> Manifest:
        return Manifest(
            name="test",
            version="1.0.0",
            sysroot_ref=Ref(RefKind.TAG, "0.20.0"),
            dependencies=[
                Dependency(
                    n,
                    f"nanvix/{n}",
                    Ref(RefKind.VERSION, f"1.0.0{SDK_SUFFIX}"),
                )
                for n in dep_names
            ],
            toolchain=Toolchain(
                ToolchainKind.SDK,
                SdkPin(
                    self.contract.sdk_version,
                    self.contract.provider_id,
                    self.contract.image.name,
                    self.contract.image.digest,
                ),
            ),
        )

    def _pkg(self, name: str) -> ResolvedPackage:
        return ResolvedPackage(
            name=name,
            repo=f"nanvix/{name}",
            kind="dependency",
            ref=Ref(RefKind.VERSION, f"1.0.0{SDK_SUFFIX}"),
            resolved_tag=f"1.0.0{SDK_SUFFIX}",
            resolved_commitish="e" * 40,
            release_id=9,
        )

    def _run(
        self,
        manifest: Manifest,
        graph: dict[str, list[str]],
        overrides: dict[str, str] | None,
    ) -> Lockfile | BlockedResolution:
        """Resolve *manifest* with a mocked dependency *graph*.

        *graph* maps each package name to the names of its transitive
        deps (as they would appear in its shipped ``nanvix.lock``).
        """

        def fake_release(
            repo: str, specifier: object, **_kw: object
        ) -> dict[str, object]:
            if repo == "nanvix/nanvix":
                return release("v0.20.0", "c" * 40, 1)
            return release(f"1.0.0{SDK_SUFFIX}", "e" * 40, 2)

        def fake_download(
            dep_release: object,
            cache_dir: object,
            *,
            gh_token: str | None = None,
            dep_name: str | None = None,
        ) -> Lockfile:
            children = graph.get(dep_name or "", [])
            return Lockfile(
                LockfileMetadata(
                    manifest_hash="sha256:dep",
                    nanvix_zutil_version="0.15.0",
                    sdk=self.contract.provenance(),
                    shallow=True,
                ),
                packages=[self._pkg(c) for c in children],
            )

        with (
            patch(
                "nanvix_zutil.resolver.resolve_sdk_release",
                return_value=self.contract,
            ),
            patch(
                "nanvix_zutil.resolver.github.resolve_release",
                side_effect=fake_release,
            ),
            patch(
                "nanvix_zutil.resolver.download_lockfile_asset",
                side_effect=fake_download,
            ),
        ):
            return resolve(
                manifest,
                cache_dir=Path.cwd() / "cache",
                local_overrides=overrides,
            )

    def test_released_parent_of_override_fatals(self) -> None:
        """sqlite (released) -> zlib (overridden): fatal."""
        with self.assertRaises(SystemExit) as ctx:
            self._run(
                self._manifest("sqlite", "zlib"),
                {"sqlite": ["zlib"]},
                {"zlib": "/p/zlib"},
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_all_overridden_passes(self) -> None:
        """Both parent and leaf overridden: no fatal."""
        result = self._run(
            self._manifest("sqlite", "zlib"),
            {"sqlite": ["zlib"]},
            {"zlib": "/p/zlib", "sqlite": "/p/sqlite"},
        )
        self.assertNotIsInstance(result, BlockedResolution)

    def test_override_with_no_released_parent_passes(self) -> None:
        """Overriding a leaf nothing released depends on: no fatal."""
        result = self._run(
            self._manifest("zlib"),
            {},
            {"zlib": "/p/zlib"},
        )
        self.assertNotIsInstance(result, BlockedResolution)

    def test_multi_hop_fatals(self) -> None:
        """cpython -> sqlite -> zlib; overriding only zlib: fatal."""
        with self.assertRaises(SystemExit) as ctx:
            self._run(
                self._manifest("cpython"),
                {"cpython": ["sqlite"], "sqlite": ["zlib"]},
                {"zlib": "/p/zlib"},
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_no_overrides_is_noop(self) -> None:
        """Absent overrides, resolution is unchanged."""
        result = self._run(
            self._manifest("sqlite", "zlib"),
            {"sqlite": ["zlib"]},
            None,
        )
        self.assertNotIsInstance(result, BlockedResolution)


if __name__ == "__main__":
    unittest.main()
