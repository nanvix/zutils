# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.commands.resolve."""

import os
import sys
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from nanvix_zutil import paths
from nanvix_zutil.buildroot import Ref, RefKind
from nanvix_zutil.commands.resolve import main
from nanvix_zutil.lockfile import Lockfile, LockfileMetadata, ResolvedPackage
from nanvix_zutil.manifest import Manifest, SdkPin, Toolchain, ToolchainKind
from nanvix_zutil.sdk import SdkImage, SdkProvenance
from tests.testutils import make_sdk_provenance

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TAG = "v0.12.266"
_SHA = "fa06b88abcdef1234567890abcdef1234567890ab"
_NAME = "zlib"
_VERSION = "1.3.1"


def _make_manifest() -> Manifest:
    """Return a minimal fake Manifest."""
    return _make_sdk_manifest()


def _make_lockfile() -> Lockfile:
    """Return a Lockfile with a sysroot package."""
    return Lockfile(
        metadata=LockfileMetadata(
            manifest_hash="sha256:abc123",
            nanvix_zutil_version="0.3.0",
            sdk=make_sdk_provenance("0.20.0"),
        ),
        packages=[
            ResolvedPackage(
                name="nanvix",
                repo="nanvix/nanvix",
                kind="sysroot",
                ref=Ref(kind=RefKind.TAG, value="0.12.266"),
                resolved_tag=_TAG,
                resolved_commitish=_SHA,
                release_id=12345,
            ),
        ],
    )


def _make_lockfile_no_sysroot() -> Lockfile:
    """Return a Lockfile without a sysroot package."""
    return Lockfile(
        metadata=LockfileMetadata(
            manifest_hash="sha256:abc123",
            nanvix_zutil_version="0.3.0",
            sdk=make_sdk_provenance("0.20.0"),
        ),
        packages=[],
    )


def _make_sdk_manifest() -> Manifest:
    """Return a fake canonical SDK manifest."""
    digest = f"sha256:{'a' * 64}"
    return Manifest(
        name=_NAME,
        version=_VERSION,
        sysroot_ref=Ref(kind=RefKind.TAG, value="0.20.0"),
        toolchain=Toolchain(
            ToolchainKind.SDK,
            SdkPin(
                version="v0.20.0-sdk.1",
                provider_id="c-clang",
                image="ghcr.io/nanvix/nanvix-sdk-c-clang",
                digest=digest,
            ),
        ),
    )


def _make_sdk_lockfile() -> Lockfile:
    """Return a lockfile carrying verified SDK provenance."""
    image = SdkImage(
        name="ghcr.io/nanvix/nanvix-sdk-c-clang",
        digest=f"sha256:{'a' * 64}",
        ref=("ghcr.io/nanvix/nanvix-sdk-c-clang@" f"sha256:{'a' * 64}"),
    )
    provenance = SdkProvenance(
        sdk_version="v0.20.0-sdk.1",
        provider_id="c-clang",
        provider="clang",
        role="c",
        image=image,
        nanvix_tag="v0.20.0",
        nanvix_version="0.20.0",
        nanvix_commit="b" * 40,
        sysroot_sha256="c" * 64,
        compat={"c_abi": "i686-nanvix-sysv-1"},
    )
    lockfile = _make_lockfile()
    lockfile.metadata.sdk = provenance
    return lockfile


def _write_manifest() -> None:
    """Write a minimal manifest into the autouse-fixture .nanvix/ dir."""
    (paths.manifest_path()).write_text('[package]\nname="zlib"\nversion="1.3.1"\n')


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDefaultOutput(unittest.TestCase):
    """Default output emits key=value lines."""

    @patch("nanvix_zutil.commands.resolve.resolve")
    @patch("nanvix_zutil.commands.resolve.load_manifest")
    def test_key_value_output(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        _write_manifest()

        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-zutil resolve"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__

        self.assertEqual(ctx.exception.code, 0)
        output = buf.getvalue()
        self.assertIn(f"nanvix_tag={_TAG}", output)
        self.assertIn(f"nanvix_sha={_SHA[:7]}", output)
        self.assertIn(f"nanvix_version={_TAG.lstrip('v')}", output)
        self.assertIn(f"package_name={_NAME}", output)
        self.assertIn(f"package_version={_VERSION}", output)
        self.assertIn("sdk_version=v0.20.0-sdk.1", output)

    @patch("nanvix_zutil.commands.resolve.resolve")
    @patch("nanvix_zutil.commands.resolve.load_manifest")
    def test_sdk_key_value_output(
        self,
        mock_load: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        mock_load.return_value = _make_sdk_manifest()
        mock_resolve.return_value = _make_sdk_lockfile()
        _write_manifest()

        output = StringIO()
        with (
            patch("sys.argv", ["nanvix-zutil resolve"]),
            patch("sys.stdout", output),
            self.assertRaises(SystemExit) as context,
        ):
            main()

        self.assertEqual(context.exception.code, 0)
        values = dict(
            line.split("=", maxsplit=1) for line in output.getvalue().splitlines()
        )
        self.assertEqual(values["sdk_version"], "v0.20.0-sdk.1")
        self.assertEqual(values["sdk_provider_id"], "c-clang")
        self.assertEqual(values["sdk_provider"], "clang")
        self.assertEqual(
            values["sdk_image"],
            "ghcr.io/nanvix/nanvix-sdk-c-clang",
        )
        self.assertEqual(values["sdk_digest"], f"sha256:{'a' * 64}")
        self.assertEqual(
            values["sdk_image_ref"],
            "ghcr.io/nanvix/nanvix-sdk-c-clang@" f"sha256:{'a' * 64}",
        )
        self.assertEqual(values["sdk_c_abi"], "i686-nanvix-sysv-1")
        self.assertEqual(values["sdk_libc_tag"], "v0.20.0")
        self.assertEqual(values["sdk_libc_commit"], "b" * 40)
        self.assertEqual(values["sdk_sysroot_sha256"], "c" * 64)


class TestShallowFlag(unittest.TestCase):
    """``--shallow`` is passed through to resolve()."""

    @patch("nanvix_zutil.commands.resolve.resolve")
    @patch("nanvix_zutil.commands.resolve.load_manifest")
    def test_shallow_passed(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        _write_manifest()

        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-zutil resolve", "--shallow"]),
                self.assertRaises(SystemExit),
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__

        mock_resolve.assert_called_once()
        call_kwargs = mock_resolve.call_args
        self.assertTrue(call_kwargs.kwargs.get("shallow", False))


class TestGhToken(unittest.TestCase):
    """``--gh-token`` and ``GH_TOKEN`` env var handling."""

    @patch("nanvix_zutil.commands.resolve.resolve")
    @patch("nanvix_zutil.commands.resolve.load_manifest")
    def test_gh_token_from_env(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        _write_manifest()

        os.environ["GH_TOKEN"] = "env-token"
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-zutil resolve"]),
                self.assertRaises(SystemExit),
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
            os.environ.pop("GH_TOKEN", None)

        call_kwargs = mock_resolve.call_args
        self.assertEqual(call_kwargs.kwargs.get("gh_token"), "env-token")

    @patch("nanvix_zutil.commands.resolve.resolve")
    @patch("nanvix_zutil.commands.resolve.load_manifest")
    def test_gh_token_cli_overrides_env(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        _write_manifest()

        os.environ["GH_TOKEN"] = "env-token"
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch(
                    "sys.argv",
                    ["nanvix-zutil resolve", "--gh-token=cli-token"],
                ),
                self.assertRaises(SystemExit),
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
            os.environ.pop("GH_TOKEN", None)

        call_kwargs = mock_resolve.call_args
        self.assertEqual(call_kwargs.kwargs.get("gh_token"), "cli-token")


class TestMissingManifest(unittest.TestCase):
    """Missing manifest exits with code 3."""

    def test_missing_manifest_exits_3(self) -> None:
        # Autouse fixture provides an empty .nanvix/ (no nanvix.toml).
        with (
            patch("sys.argv", ["nanvix-zutil resolve"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 3)


class TestNoSysroot(unittest.TestCase):
    """No sysroot in lockfile exits with code 3."""

    @patch("nanvix_zutil.commands.resolve.resolve")
    @patch("nanvix_zutil.commands.resolve.load_manifest")
    def test_no_sysroot_exits_3(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile_no_sysroot()

        _write_manifest()

        with (
            patch("sys.argv", ["nanvix-zutil resolve"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
