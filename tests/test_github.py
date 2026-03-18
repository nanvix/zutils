# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.github."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
import nanvix_zutil.github as github_mod


def _make_urlopen_response(body: bytes) -> MagicMock:
    """Return a context-manager mock that yields a response with *body*."""
    resp = MagicMock()
    resp.read.return_value = body
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _make_release_payload(asset_name: str, download_url: str) -> bytes:
    """Return a minimal GitHub releases API response."""
    return json.dumps(
        {
            "assets": [
                {
                    "name": asset_name,
                    "browser_download_url": download_url,
                }
            ]
        }
    ).encode()


class TestDownloadReleaseAssetAlreadyExists(unittest.TestCase):
    """download_release_asset skips the download when the file exists."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_returns_existing_path_without_network(self) -> None:
        dest = Path(self._tmpdir.name)
        asset = dest / "my-asset.tar.bz2"
        asset.write_bytes(b"data")

        with patch("urllib.request.urlopen") as mock_open:
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                tag="v1.0.0",
                asset_name="my-asset.tar.bz2",
                dest=dest,
            )
            mock_open.assert_not_called()

        self.assertEqual(result, asset)


class TestDownloadReleaseAssetSuccess(unittest.TestCase):
    """download_release_asset fetches the file on a cache miss."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_downloads_and_writes_file(self) -> None:
        dest = Path(self._tmpdir.name)
        asset_name = "zlib-hyperlight-multiprocess-128mb.tar.bz2"
        download_url = "https://example.com/" + asset_name
        file_content = b"archive-bytes"

        metadata_resp = _make_urlopen_response(
            _make_release_payload(asset_name, download_url)
        )
        file_resp = _make_urlopen_response(file_content)

        with patch("urllib.request.urlopen", side_effect=[metadata_resp, file_resp]):
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                tag="v1.0.0",
                asset_name=asset_name,
                dest=dest,
            )

        self.assertTrue(result.exists())
        self.assertEqual(result.read_bytes(), file_content)

    def test_gh_token_added_to_headers(self) -> None:
        dest = Path(self._tmpdir.name)
        asset_name = "asset.tar.bz2"
        download_url = "https://example.com/" + asset_name

        metadata_resp = _make_urlopen_response(
            _make_release_payload(asset_name, download_url)
        )
        file_resp = _make_urlopen_response(b"content")

        captured_requests: list[object] = []

        def capture_urlopen(req: object) -> object:
            captured_requests.append(req)
            if len(captured_requests) == 1:
                return metadata_resp
            return file_resp

        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            github_mod.download_release_asset(
                repo="nanvix/zlib",
                tag="v1.0.0",
                asset_name=asset_name,
                dest=dest,
                gh_token="mytoken",
            )

        import urllib.request

        first_req = captured_requests[0]
        self.assertIsInstance(first_req, urllib.request.Request)
        assert isinstance(first_req, urllib.request.Request)
        self.assertIn("Authorization", first_req.headers)


class TestDownloadReleaseAssetNotFound(unittest.TestCase):
    """download_release_asset exits with code 3 when the asset is absent."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_asset_not_in_release_exits_3(self) -> None:
        dest = Path(self._tmpdir.name)
        # Release has no matching asset.
        metadata_resp = _make_urlopen_response(json.dumps({"assets": []}).encode())

        with patch("urllib.request.urlopen", return_value=metadata_resp):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.download_release_asset(
                    repo="nanvix/zlib",
                    tag="v1.0.0",
                    asset_name="missing.tar.bz2",
                    dest=dest,
                )
        self.assertEqual(ctx.exception.code, 3)


class TestDownloadReleaseAssetNetworkError(unittest.TestCase):
    """download_release_asset exits with code 4 after all retries fail."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_metadata_network_error_exits_4(self) -> None:
        dest = Path(self._tmpdir.name)

        with (
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("connection refused"),
            ),
            patch("time.sleep"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.download_release_asset(
                    repo="nanvix/zlib",
                    tag="v1.0.0",
                    asset_name="asset.tar.bz2",
                    dest=dest,
                )
        self.assertEqual(ctx.exception.code, 4)

    def test_download_network_error_exits_4(self) -> None:
        dest = Path(self._tmpdir.name)
        asset_name = "asset.tar.bz2"
        download_url = "https://example.com/" + asset_name

        metadata_resp = _make_urlopen_response(
            _make_release_payload(asset_name, download_url)
        )

        call_count = 0

        def side_effect(req: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return metadata_resp
            raise urllib.error.URLError("download failed")

        with (
            patch("urllib.request.urlopen", side_effect=side_effect),
            patch("time.sleep"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.download_release_asset(
                    repo="nanvix/zlib",
                    tag="v1.0.0",
                    asset_name=asset_name,
                    dest=dest,
                )
        self.assertEqual(ctx.exception.code, 4)

    def test_unexpected_api_response_exits_4(self) -> None:
        dest = Path(self._tmpdir.name)
        # API returns a list instead of a dict.
        bad_resp = _make_urlopen_response(json.dumps([1, 2, 3]).encode())

        with patch("urllib.request.urlopen", return_value=bad_resp):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.download_release_asset(
                    repo="nanvix/zlib",
                    tag="v1.0.0",
                    asset_name="asset.tar.bz2",
                    dest=dest,
                )
        self.assertEqual(ctx.exception.code, 4)


if __name__ == "__main__":
    unittest.main()
