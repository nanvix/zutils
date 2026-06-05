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
from nanvix_zutil.manifest import Manifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TAG = "v0.12.266"
_SHA = "fa06b88abcdef1234567890abcdef1234567890ab"
_NAME = "zlib"
_VERSION = "1.3.1"


def _make_manifest() -> Manifest:
    """Return a minimal fake Manifest."""
    return Manifest(
        name=_NAME,
        version=_VERSION,
        sysroot_ref=Ref(kind=RefKind.TAG, value="0.12.266"),
    )


def _make_lockfile() -> Lockfile:
    """Return a Lockfile with a sysroot package."""
    return Lockfile(
        metadata=LockfileMetadata(
            manifest_hash="sha256:abc123",
            nanvix_zutil_version="0.3.0",
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
        ),
        packages=[],
    )


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
