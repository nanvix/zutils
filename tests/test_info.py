# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.info."""

import json
import sys
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from nanvix_zutil.info import (
    NanvixInfo,
    get_nanvix_info,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MACHINE = "microvm"
_MODE = "standalone"
_MEMORY = "256mb"
_SHA = "fa06b88"
_TAG = "v0.4.1"
_VERSION = "0.4.1"
_TARGET = "x86"
_ASSET_NAME = f"nanvix-{_TARGET}-{_MACHINE}-{_MODE}-release-{_MEMORY}-{_SHA}.tar.bz2"


def _make_release(
    tag: str = _TAG,
    name: str = f"Nanvix {_VERSION}",
    sha: str = _SHA,
    target: str = _TARGET,
    machine: str = _MACHINE,
    mode: str = _MODE,
    memory: str = _MEMORY,
) -> dict[str, object]:
    """Return a minimal fake GitHub release dictionary."""
    asset_name = f"nanvix-{target}-{machine}-{mode}-release-{memory}-{sha}.tar.bz2"
    return {
        "tag_name": tag,
        "name": name,
        "target_commitish": sha * 6,  # 42-char fake full hash
        "assets": [
            {
                "name": asset_name,
                "browser_download_url": (
                    f"https://example.com/releases/download/{tag}/{asset_name}"
                ),
            }
        ],
    }


# ---------------------------------------------------------------------------
# NanvixInfo unit tests
# ---------------------------------------------------------------------------


class TestNanvixInfo(unittest.TestCase):
    """Tests for the NanvixInfo dataclass."""

    def test_to_dict_with_version(self) -> None:
        info = NanvixInfo(tag=_TAG, sha=_SHA, version=_VERSION)
        d = info.to_dict()
        self.assertEqual(d["tag"], _TAG)
        self.assertEqual(d["sha"], _SHA)
        self.assertEqual(d["version"], _VERSION)

    def test_to_dict_without_version(self) -> None:
        info = NanvixInfo(tag=_TAG, sha=_SHA, version=None)
        d = info.to_dict()
        self.assertNotIn("version", d)
        self.assertEqual(d["tag"], _TAG)
        self.assertEqual(d["sha"], _SHA)

    def test_attributes(self) -> None:
        info = NanvixInfo(tag=_TAG, sha=_SHA, version=_VERSION)
        self.assertEqual(info.tag, _TAG)
        self.assertEqual(info.sha, _SHA)
        self.assertEqual(info.version, _VERSION)


# ---------------------------------------------------------------------------
# get_nanvix_info unit tests
# ---------------------------------------------------------------------------


class TestGetNanvixInfo(unittest.TestCase):
    """Tests for get_nanvix_info."""

    @patch("nanvix_zutil.info.resolve_release")
    def test_returns_nanvix_info(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release()
        info = get_nanvix_info()
        self.assertIsInstance(info, NanvixInfo)
        self.assertEqual(info.tag, _TAG)
        self.assertEqual(info.sha, _SHA)
        self.assertEqual(info.version, _VERSION)

    @patch("nanvix_zutil.info.resolve_release")
    def test_no_version_in_release_name(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release(name="Nanvix release")
        info = get_nanvix_info()
        self.assertIsNone(info.version)

    @patch("nanvix_zutil.info.resolve_release")
    def test_v_prefixed_semver_in_release_name(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release(name="Release v1.2.3")
        info = get_nanvix_info()
        self.assertEqual(info.version, "1.2.3")

    @patch("nanvix_zutil.info.resolve_release")
    def test_custom_machine_mode_memory(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release(
            machine="microvm", mode="standalone", memory="256mb", sha="deadbee"
        )
        info = get_nanvix_info(machine="microvm", mode="standalone", memory="256mb")
        self.assertEqual(info.sha, "deadbee")

    @patch("nanvix_zutil.info.resolve_release")
    def test_single_process_mode(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release(mode="single-process", sha="abc1234")
        info = get_nanvix_info(mode="single-process")
        self.assertEqual(info.sha, "abc1234")

    @patch("nanvix_zutil.info.resolve_release")
    def test_asset_prefix_mismatch_wrong_machine_exits(
        self, mock_resolve: MagicMock
    ) -> None:
        # Release has microvm assets but caller asks for hyperlight — should fail.
        mock_resolve.return_value = _make_release(machine=_MACHINE)
        with self.assertRaises(SystemExit) as ctx:
            get_nanvix_info(machine="hyperlight")
        self.assertEqual(ctx.exception.code, 3)  # EXIT_MISSING_DEP

    @patch("nanvix_zutil.info.resolve_release")
    def test_missing_asset_exits(self, mock_resolve: MagicMock) -> None:
        release = _make_release()
        # Replace assets with ones that don't match
        release["assets"] = [
            {"name": "something-else.tar.bz2", "browser_download_url": "https://x"}
        ]
        mock_resolve.return_value = release
        with self.assertRaises(SystemExit) as ctx:
            get_nanvix_info()
        self.assertEqual(ctx.exception.code, 3)  # EXIT_MISSING_DEP

    @patch("nanvix_zutil.info.resolve_release")
    def test_empty_assets_exits(self, mock_resolve: MagicMock) -> None:
        release = _make_release()
        release["assets"] = []
        mock_resolve.return_value = release
        with self.assertRaises(SystemExit) as ctx:
            get_nanvix_info()
        self.assertEqual(ctx.exception.code, 3)  # EXIT_MISSING_DEP

    @patch("nanvix_zutil.info.resolve_release")
    def test_missing_tag_name_exits(self, mock_resolve: MagicMock) -> None:
        release = _make_release()
        del release["tag_name"]
        mock_resolve.return_value = release
        with self.assertRaises(SystemExit) as ctx:
            get_nanvix_info()
        self.assertEqual(ctx.exception.code, 4)  # EXIT_NETWORK_ERROR


# ---------------------------------------------------------------------------
# CLI (main) tests
# ---------------------------------------------------------------------------


class TestNanvixInfoMain(unittest.TestCase):
    """Tests for the nanvix-info CLI entry point."""

    @patch("nanvix_zutil.info.resolve_release")
    def test_default_output_key_value(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release()
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-info"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(ctx.exception.code, 0)
        output = buf.getvalue()
        self.assertIn(f"tag={_TAG}", output)
        self.assertIn(f"sha={_SHA}", output)
        self.assertIn(f"version={_VERSION}", output)

    @patch("nanvix_zutil.info.resolve_release")
    def test_default_args_sent_to_resolve(self, mock_resolve: MagicMock) -> None:
        """CLI defaults: repo=nanvix/nanvix, version=latest."""
        mock_resolve.return_value = _make_release()
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-info"]),
                self.assertRaises(SystemExit),
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        call_args = mock_resolve.call_args
        self.assertEqual(call_args[0][0], "nanvix/nanvix")
        self.assertEqual(call_args[0][1], "latest")

    @patch("nanvix_zutil.info.resolve_release")
    def test_json_output(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release()
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-info", "--json"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(ctx.exception.code, 0)
        obj = json.loads(buf.getvalue().strip())
        self.assertEqual(obj["tag"], _TAG)
        self.assertEqual(obj["sha"], _SHA)
        self.assertEqual(obj["version"], _VERSION)

    @patch("nanvix_zutil.info.resolve_release")
    def test_json_output_without_version(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release(name="no version here")
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-info", "--json"]),
                self.assertRaises(SystemExit),
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        obj = json.loads(buf.getvalue().strip())
        self.assertNotIn("version", obj)

    def test_invalid_repo_format_exits(self) -> None:
        with (
            patch("sys.argv", ["nanvix-info", "--repo", "invalid"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 2)  # EXIT_INVALID_ARGS

    @patch("nanvix_zutil.info.resolve_release")
    def test_custom_repo_passed_to_resolve(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release()
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-info", "--repo", "nanvix/zlib"]),
                self.assertRaises(SystemExit),
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        call_args = mock_resolve.call_args
        self.assertEqual(call_args[0][0], "nanvix/zlib")

    @patch("nanvix_zutil.info.resolve_release")
    def test_gh_token_from_env(self, mock_resolve: MagicMock) -> None:
        import os

        mock_resolve.return_value = _make_release()
        os.environ["GH_TOKEN"] = "test-token"
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-info"]),
                self.assertRaises(SystemExit),
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
            os.environ.pop("GH_TOKEN", None)
        call_args = mock_resolve.call_args
        # gh_token is the third positional arg
        self.assertEqual(call_args[0][2], "test-token")

    @patch("nanvix_zutil.info.resolve_release")
    def test_gh_token_cli_overrides_env(self, mock_resolve: MagicMock) -> None:
        import os

        mock_resolve.return_value = _make_release()
        os.environ["GH_TOKEN"] = "env-token"
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-info", "--gh-token", "cli-token"]),
                self.assertRaises(SystemExit),
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
            os.environ.pop("GH_TOKEN", None)
        call_args = mock_resolve.call_args
        self.assertEqual(call_args[0][2], "cli-token")

    @patch("nanvix_zutil.info.resolve_release")
    def test_custom_machine_mode_memory_flags(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = _make_release(
            machine="microvm", mode="standalone", memory="256mb", sha="deadbee"
        )
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch(
                    "sys.argv",
                    [
                        "nanvix-info",
                        "--machine",
                        "microvm",
                        "--mode",
                        "standalone",
                        "--memory",
                        "256mb",
                    ],
                ),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(ctx.exception.code, 0)
        output = buf.getvalue()
        self.assertIn("sha=deadbee", output)


class TestNanvixZutilInfoInvocation(unittest.TestCase):
    """Tests for invocation via ``nanvix-zutil info`` (CLI dispatcher)."""

    @patch("nanvix_zutil.info.resolve_release")
    def test_nanvix_zutil_info_key_value(self, mock_resolve: MagicMock) -> None:
        """nanvix-zutil info produces same output as nanvix-info."""
        mock_resolve.return_value = _make_release()
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-zutil info"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(ctx.exception.code, 0)
        output = buf.getvalue()
        self.assertIn(f"tag={_TAG}", output)
        self.assertIn(f"sha={_SHA}", output)

    @patch("nanvix_zutil.info.resolve_release")
    def test_nanvix_zutil_info_json(self, mock_resolve: MagicMock) -> None:
        """nanvix-zutil info --json produces valid JSON."""
        mock_resolve.return_value = _make_release()
        buf = StringIO()
        sys.stdout = buf
        try:
            with (
                patch("sys.argv", ["nanvix-zutil info", "--json"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                main()
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(ctx.exception.code, 0)
        obj = json.loads(buf.getvalue().strip())
        self.assertEqual(obj["tag"], _TAG)
        self.assertEqual(obj["sha"], _SHA)


if __name__ == "__main__":
    unittest.main()
