# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Focused tests for SDK-only resolver helpers and staleness."""

from __future__ import annotations

import unittest

# pyright: reportPrivateUsage=false

from nanvix_zutil import paths
from nanvix_zutil.buildroot import Ref, RefKind
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    ResolvedPackage,
    compute_manifest_hash,
)
from nanvix_zutil.resolver import _collect_assets, _detect_cycles, is_stale
from tests.testutils import MINIMAL_MANIFEST, make_sdk_provenance


class TestResolverHelpers(unittest.TestCase):
    """Asset filtering and cycle detection remain deterministic."""

    def test_collects_supported_archives_only(self) -> None:
        release: dict[str, object] = {
            "assets": [
                {
                    "name": "dep.tar.bz2",
                    "browser_download_url": "https://example.invalid/dep.tar.bz2",
                    "id": 1,
                },
                {
                    "name": "nanvix.lock",
                    "browser_download_url": "https://example.invalid/nanvix.lock",
                },
            ]
        }
        assets = _collect_assets(release)
        self.assertEqual([asset.name for asset in assets], ["dep.tar.bz2"])
        self.assertEqual(assets[0].asset_id, 1)

    def test_cycle_is_rejected(self) -> None:
        packages = [
            ResolvedPackage(
                name="a",
                repo="nanvix/a",
                kind="dependency",
                ref=Ref(RefKind.VERSION, "1-nanvix-0.1.0-sdk.1"),
                resolved_tag="1-nanvix-0.1.0-sdk.1",
                resolved_commitish="a" * 40,
                release_id=1,
                dependencies=["b"],
            ),
            ResolvedPackage(
                name="b",
                repo="nanvix/b",
                kind="dependency",
                ref=Ref(RefKind.VERSION, "1-nanvix-0.1.0-sdk.1"),
                resolved_tag="1-nanvix-0.1.0-sdk.1",
                resolved_commitish="b" * 40,
                release_id=2,
                dependencies=["a"],
            ),
        ]
        with self.assertRaises(SystemExit) as context:
            _detect_cycles(packages)
        self.assertEqual(context.exception.code, 2)


class TestSdkLockStaleness(unittest.TestCase):
    """Manifest and immutable SDK coordinates determine staleness."""

    def setUp(self) -> None:
        paths.manifest_path().write_text(MINIMAL_MANIFEST)
        self.lock = Lockfile(
            LockfileMetadata(
                manifest_hash=compute_manifest_hash(paths.manifest_path()),
                nanvix_zutil_version="0.15.3",
                sdk=make_sdk_provenance(),
            )
        )

    def test_matching_lock_is_current(self) -> None:
        self.assertFalse(is_stale(self.lock))

    def test_manifest_change_is_stale(self) -> None:
        paths.manifest_path().write_text(MINIMAL_MANIFEST + "\n")
        self.assertTrue(is_stale(self.lock))

    def test_sdk_revision_change_is_stale(self) -> None:
        stale = make_sdk_provenance()
        object.__setattr__(stale, "sdk_version", "v0.1.0-sdk.2")
        self.lock.metadata.sdk = stale
        self.assertTrue(is_stale(self.lock))

    def test_shallow_mode_change_is_stale(self) -> None:
        self.assertTrue(is_stale(self.lock, shallow=True))


if __name__ == "__main__":
    unittest.main()
