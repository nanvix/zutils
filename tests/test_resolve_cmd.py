# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.resolve_cmd."""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanvix_zutil.buildroot import Ref, RefKind
from nanvix_zutil.lockfile import Lockfile, LockfileMetadata, ResolvedPackage
from nanvix_zutil.manifest import Manifest
from nanvix_zutil.resolve_cmd import main

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDefaultOutput(unittest.TestCase):
    """Default output emits key=value lines."""

    @patch("nanvix_zutil.resolve_cmd.resolve")
    @patch("nanvix_zutil.resolve_cmd.load_manifest")
    def test_key_value_output(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text('[package]\nname="zlib"\nversion="1.3.1"\n')

            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv", ["nanvix-zutil resolve", f"--manifest={manifest}"]
                    ),
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


class TestJsonOutput(unittest.TestCase):
    """``--json`` emits a JSON object."""

    @patch("nanvix_zutil.resolve_cmd.resolve")
    @patch("nanvix_zutil.resolve_cmd.load_manifest")
    def test_json_output(self, mock_load: MagicMock, mock_resolve: MagicMock) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text('[package]\nname="zlib"\nversion="1.3.1"\n')

            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv",
                        ["nanvix-zutil resolve", f"--manifest={manifest}", "--json"],
                    ),
                    self.assertRaises(SystemExit) as ctx,
                ):
                    main()
            finally:
                sys.stdout = sys.__stdout__

            self.assertEqual(ctx.exception.code, 0)
            obj = json.loads(buf.getvalue().strip())
            self.assertEqual(obj["nanvix_tag"], _TAG)
            self.assertEqual(obj["nanvix_sha"], _SHA[:7])
            self.assertEqual(obj["nanvix_version"], _TAG.lstrip("v"))
            self.assertEqual(obj["package_name"], _NAME)
            self.assertEqual(obj["package_version"], _VERSION)


class TestShallowFlag(unittest.TestCase):
    """``--shallow`` is passed through to resolve()."""

    @patch("nanvix_zutil.resolve_cmd.resolve")
    @patch("nanvix_zutil.resolve_cmd.load_manifest")
    def test_shallow_passed(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text('[package]\nname="zlib"\nversion="1.3.1"\n')

            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv",
                        [
                            "nanvix-zutil resolve",
                            f"--manifest={manifest}",
                            "--shallow",
                        ],
                    ),
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

    @patch("nanvix_zutil.resolve_cmd.resolve")
    @patch("nanvix_zutil.resolve_cmd.load_manifest")
    def test_gh_token_from_env(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text('[package]\nname="zlib"\nversion="1.3.1"\n')

            os.environ["GH_TOKEN"] = "env-token"
            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv",
                        ["nanvix-zutil resolve", f"--manifest={manifest}"],
                    ),
                    self.assertRaises(SystemExit),
                ):
                    main()
            finally:
                sys.stdout = sys.__stdout__
                os.environ.pop("GH_TOKEN", None)

            call_kwargs = mock_resolve.call_args
            self.assertEqual(call_kwargs.kwargs.get("gh_token"), "env-token")

    @patch("nanvix_zutil.resolve_cmd.resolve")
    @patch("nanvix_zutil.resolve_cmd.load_manifest")
    def test_gh_token_cli_overrides_env(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text('[package]\nname="zlib"\nversion="1.3.1"\n')

            os.environ["GH_TOKEN"] = "env-token"
            buf = StringIO()
            sys.stdout = buf
            try:
                with (
                    patch(
                        "sys.argv",
                        [
                            "nanvix-zutil resolve",
                            f"--manifest={manifest}",
                            "--gh-token=cli-token",
                        ],
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
        with (
            patch(
                "sys.argv",
                ["nanvix-zutil resolve", "--manifest=/nonexistent/nanvix.toml"],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 3)


class TestNoSysroot(unittest.TestCase):
    """No sysroot in lockfile exits with code 3."""

    @patch("nanvix_zutil.resolve_cmd.resolve")
    @patch("nanvix_zutil.resolve_cmd.load_manifest")
    def test_no_sysroot_exits_3(
        self, mock_load: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_load.return_value = _make_manifest()
        mock_resolve.return_value = _make_lockfile_no_sysroot()

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "nanvix.toml"
            manifest.write_text('[package]\nname="zlib"\nversion="1.3.1"\n')

            with (
                patch(
                    "sys.argv",
                    ["nanvix-zutil resolve", f"--manifest={manifest}"],
                ),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
            self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
