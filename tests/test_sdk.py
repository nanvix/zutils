# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# pyright: reportPrivateUsage=false

"""Tests for SDK contracts, provenance, and release coordinates."""

from __future__ import annotations

import copy
import unittest
from typing import cast

from nanvix_zutil.sdk import (
    SDK_LABEL_PREFIX,
    SdkValidationError,
    _release_asset,
    consumer_release_tag,
    sdk_consumer_release_tag,
    validate_sdk_release,
    verify_sdk_metadata,
)


def make_sdk_contract(
    version: str = "v0.20.0-sdk.1",
    digest: str = f"sha256:{'a' * 64}",
) -> dict[str, object]:
    """Build a valid SDK release fixture."""
    image = "ghcr.io/nanvix/nanvix-sdk-c-clang"
    return {
        "schema_version": 1,
        "sdk_version": version,
        "provider_id": "c-clang",
        "provider": "clang",
        "role": "c",
        "image": {"name": image, "digest": digest, "ref": f"{image}@{digest}"},
        "target": {
            "triple": "i686-unknown-nanvix",
            "alias": "i686-nanvix",
        },
        "toolchain": {
            "llvm_version": "22.1.8",
            "llvm_commit": "b" * 40,
            "port_branch": "nanvix/v22.1.8",
        },
        "libc": {
            "nanvix_tag": "v0.20.0",
            "nanvix_version": "0.20.0",
            "nanvix_commit": "c" * 40,
            "sysroot_sha256": "d" * 64,
        },
        "compat": {
            "c_abi": "i686-nanvix-sysv-1",
            "cxx_abi": "libc++",
            "abi": "static-elf",
            "min_nanvix_os": "0.20.0",
        },
        "features": {
            "localization": True,
            "filesystem": True,
            "wide_chars": True,
            "dynamic_loader": False,
            "compiler_rt": "builtins-only",
        },
    }


def labels_for(data: dict[str, object]) -> dict[str, str]:
    """Flatten an embedded SDK manifest into OCI labels."""
    result: dict[str, str] = {}

    def flatten(prefix: str, value: object) -> None:
        if isinstance(value, dict):
            for key, child in cast("dict[str, object]", value).items():
                flatten(f"{prefix}.{key}" if prefix else str(key), child)
            return
        rendered = (
            "true" if value is True else "false" if value is False else str(value)
        )
        result[f"{SDK_LABEL_PREFIX}{prefix}"] = rendered

    flatten("", data)
    return result


class TestSdkContract(unittest.TestCase):
    """SDK release schema and skew validation."""

    def test_valid_contract(self) -> None:
        contract = validate_sdk_release(make_sdk_contract())
        self.assertEqual(contract.runtime_version, "0.20.0")
        self.assertEqual(contract.revision, 1)
        self.assertTrue(contract.image.ref.endswith(contract.image.digest))

    def test_missing_field_rejected(self) -> None:
        data = make_sdk_contract()
        del data["provider_id"]
        with self.assertRaises(SdkValidationError):
            validate_sdk_release(data)

    def test_missing_canonical_feature_rejected(self) -> None:
        data = make_sdk_contract()
        features = data["features"]
        assert isinstance(features, dict)
        del features["wide_chars"]
        with self.assertRaises(SdkValidationError):
            validate_sdk_release(data)

    def test_boolean_schema_and_short_commits_rejected(self) -> None:
        boolean_schema = make_sdk_contract()
        boolean_schema["schema_version"] = True
        with self.assertRaises(SdkValidationError):
            validate_sdk_release(boolean_schema)

        short_commit = make_sdk_contract()
        toolchain = short_commit["toolchain"]
        assert isinstance(toolchain, dict)
        toolchain["llvm_commit"] = "abcdef0"
        with self.assertRaises(SdkValidationError):
            validate_sdk_release(short_commit)

        short_libc = make_sdk_contract()
        libc = short_libc["libc"]
        assert isinstance(libc, dict)
        libc["nanvix_commit"] = "abcdef0"
        with self.assertRaises(SdkValidationError):
            validate_sdk_release(short_libc)

    def test_runtime_skew_rejected(self) -> None:
        data = make_sdk_contract()
        libc = data["libc"]
        assert isinstance(libc, dict)
        libc["nanvix_version"] = "0.20.1"
        with self.assertRaises(SdkValidationError):
            validate_sdk_release(data)

    def test_mutable_image_rejected(self) -> None:
        data = make_sdk_contract()
        image = data["image"]
        assert isinstance(image, dict)
        image["ref"] = f"{image['name']}:v0.20.0-sdk.1"
        with self.assertRaises(SdkValidationError):
            validate_sdk_release(data)

    def test_oci_mismatch_rejected(self) -> None:
        contract = validate_sdk_release(make_sdk_contract())
        embedded = contract.to_dict()
        del embedded["image"]
        labels = labels_for(embedded)
        labels[f"{SDK_LABEL_PREFIX}compat.c_abi"] = "wrong"
        with self.assertRaises(SdkValidationError):
            verify_sdk_metadata(contract, embedded, labels, contract.image.digest)

    def test_schema_one_derived_release_fields(self) -> None:
        contract = validate_sdk_release(make_sdk_contract())
        embedded = contract.to_dict()
        del embedded["image"]
        del embedded["provider_id"]
        libc = embedded["libc"]
        assert isinstance(libc, dict)
        del libc["nanvix_version"]
        labels = labels_for(embedded)
        del labels[f"{SDK_LABEL_PREFIX}libc.sysroot_sha256"]
        verify_sdk_metadata(contract, embedded, labels, contract.image.digest)

    def test_present_derived_field_mismatch_is_rejected(self) -> None:
        contract = validate_sdk_release(make_sdk_contract())
        embedded = contract.to_dict()
        embedded["provider_id"] = "wrong-provider"
        labels = labels_for(
            {
                key: value
                for key, value in contract.to_dict().items()
                if key not in {"image", "provider_id"}
            }
        )
        with self.assertRaises(SdkValidationError):
            verify_sdk_metadata(
                contract,
                embedded,
                labels,
                contract.image.digest,
            )

    def test_release_contract_asset_requires_digest(self) -> None:
        digest = f"sha256:{'a' * 64}"
        release: dict[str, object] = {
            "assets": [
                {
                    "name": "sdk-release.json",
                    "browser_download_url": "https://example.invalid/sdk.json",
                    "digest": digest,
                }
            ]
        }
        self.assertEqual(
            _release_asset(release),
            ("https://example.invalid/sdk.json", digest),
        )
        asset = release["assets"]
        assert isinstance(asset, list)
        item = cast("list[object]", asset)[0]
        assert isinstance(item, dict)
        del cast("dict[str, object]", item)["digest"]
        with self.assertRaises(SdkValidationError):
            _release_asset(release)

    def test_embedded_mismatch_rejected(self) -> None:
        contract = validate_sdk_release(make_sdk_contract())
        embedded = copy.deepcopy(contract.to_dict())
        del embedded["image"]
        embedded["provider"] = "gcc"
        with self.assertRaises(SdkValidationError):
            verify_sdk_metadata(
                contract,
                embedded,
                labels_for(
                    {
                        key: value
                        for key, value in contract.to_dict().items()
                        if key != "image"
                    }
                ),
                contract.image.digest,
            )


class TestSdkReleaseNames(unittest.TestCase):
    """SDK-only revisions produce unique exact consumer tags."""

    def test_sdk_revision_changes_coordinate(self) -> None:
        first = sdk_consumer_release_tag("1.3.1", "v0.20.0-sdk.1")
        second = sdk_consumer_release_tag("1.3.1", "v0.20.0-sdk.2")
        self.assertEqual(first, "1.3.1-nanvix-0.20.0-sdk.1")
        self.assertNotEqual(first, second)

    def test_legacy_coordinate_preserved(self) -> None:
        self.assertEqual(
            consumer_release_tag("1.3.1", "0.20.0"),
            "1.3.1-nanvix-0.20.0",
        )

    def test_runtime_skew_rejected(self) -> None:
        with self.assertRaises(SdkValidationError):
            consumer_release_tag("1.3.1", "0.20.1", "v0.20.0-sdk.1")


if __name__ == "__main__":
    unittest.main()
