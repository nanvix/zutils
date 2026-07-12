# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for SDK manifest pins and lockfile provenance."""

from __future__ import annotations

import unittest

from nanvix_zutil import paths
from nanvix_zutil.lockfile import (
    Lockfile,
    LockfileMetadata,
    read_lockfile,
    write_lockfile,
)
from nanvix_zutil.manifest import ToolchainKind, load_manifest
from nanvix_zutil.sdk import validate_sdk_release
from tests.test_sdk import make_sdk_contract


class TestSdkManifest(unittest.TestCase):
    """Preferred, derived, legacy, and malformed SDK pin forms."""

    def test_immutable_sdk_pin_and_exact_dependency_tag(self) -> None:
        digest = f"sha256:{'a' * 64}"
        paths.manifest_path().write_text(
            "[package]\n"
            'name = "test"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.20.0"\n'
            "\n[toolchain]\n"
            'kind = "nanvix-sdk"\n'
            'provider = "c-clang"\n'
            'sdk-version = "v0.20.0-sdk.1"\n'
            'sdk-image = "ghcr.io/nanvix/nanvix-sdk-c-clang"\n'
            f'sdk-digest = "{digest}"\n'
            'build-image = "ghcr.io/nanvix/port-builder"\n'
            f'build-digest = "sha256:{"b" * 64}"\n'
            "\n[dependencies]\n"
            'zlib = "1.3.1"\n'
        )
        manifest = load_manifest()
        self.assertEqual(manifest.toolchain.kind, ToolchainKind.SDK)
        assert manifest.toolchain.sdk is not None
        self.assertEqual(manifest.toolchain.sdk.digest, digest)
        self.assertEqual(
            manifest.toolchain.sdk.effective_build_ref,
            f"ghcr.io/nanvix/port-builder@sha256:{'b' * 64}",
        )
        self.assertEqual(
            manifest.dependencies[0].ref.value,
            "1.3.1-nanvix-0.20.0-sdk.1",
        )

    def test_early_spelling_remains_readable(self) -> None:
        digest = f"sha256:{'a' * 64}"
        paths.manifest_path().write_text(
            "[package]\n"
            'name = "test"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.20.0"\n'
            "\n[toolchain]\n"
            'type = "sdk"\n'
            'sdk-version = "v0.20.0-sdk.1"\n'
            'provider-id = "c-clang"\n'
            'image = "ghcr.io/nanvix/nanvix-sdk-c-clang"\n'
            f'digest = "{digest}"\n'
        )
        pin = load_manifest().toolchain.sdk
        assert pin is not None
        self.assertEqual(pin.image_ref, f"{pin.image}@{digest}")

    def test_missing_toolchain_is_legacy(self) -> None:
        paths.manifest_path().write_text(
            "[package]\n"
            'name = "test"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.20.0"\n'
        )
        self.assertEqual(load_manifest().toolchain.kind, ToolchainKind.LEGACY)

    def test_sdk_runtime_skew_fails(self) -> None:
        paths.manifest_path().write_text(
            "[package]\n"
            'name = "test"\n'
            'version = "1.0.0"\n'
            'nanvix-version = "0.20.1"\n'
            "\n[toolchain]\n"
            'type = "sdk"\n'
            'version = "v0.20.0-sdk.1"\n'
            'provider = "c-clang"\n'
            f'image = "ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:{"a" * 64}"\n'
        )
        with self.assertRaises(SystemExit) as context:
            load_manifest()
        self.assertEqual(context.exception.code, 2)


class TestSdkLockfile(unittest.TestCase):
    """SDK provenance is optional, round-trippable, and shallow-aware."""

    def test_provenance_round_trip(self) -> None:
        provenance = validate_sdk_release(make_sdk_contract()).provenance()
        lock = Lockfile(
            LockfileMetadata(
                manifest_hash="sha256:manifest",
                nanvix_zutil_version="0.15.0",
                sdk=provenance,
                shallow=True,
            )
        )
        path = paths.nanvix_root() / "nanvix.lock"
        write_lockfile(lock, path)
        restored = read_lockfile(path)
        self.assertEqual(restored.metadata.sdk, provenance)
        self.assertTrue(restored.metadata.shallow)

    def test_legacy_lock_remains_readable(self) -> None:
        path = paths.nanvix_root() / "nanvix.lock"
        path.write_text(
            "[metadata]\n"
            'manifest-hash = "sha256:x"\n'
            'nanvix-zutil-version = "0.14.0"\n'
        )
        restored = read_lockfile(path)
        self.assertIsNone(restored.metadata.sdk)
        self.assertFalse(restored.metadata.shallow)


if __name__ == "__main__":
    unittest.main()
