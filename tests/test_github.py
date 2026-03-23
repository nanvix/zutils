# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.github."""

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.log as log_mod
import nanvix_zutil.github as github_mod


def _make_urlopen_response(body: bytes, chunked: bool = False) -> MagicMock:
    """Return a context-manager mock that yields a response with *body*.

    Args:
        body: The response bytes to return.
        chunked: When ``True``, simulate a chunked read by returning *body*
            on the first ``read()`` call then ``b""`` on subsequent calls
            (matching the download loop in :func:`github.download_release_asset`).
    """
    resp = MagicMock()
    if chunked:
        resp.read.side_effect = [body, b""]
    else:
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
        asset_name = "zlib-hyperlight-multi-process-128mb.tar.bz2"
        download_url = "https://example.com/" + asset_name
        file_content = b"archive-bytes"

        metadata_resp = _make_urlopen_response(
            _make_release_payload(asset_name, download_url)
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

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
        file_resp = _make_urlopen_response(b"content", chunked=True)

        captured_requests: list[object] = []

        def capture_urlopen(req: object, **kwargs: object) -> object:
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

        def side_effect(req: object, **kwargs: object) -> object:
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


class TestDownloadReleaseAssetLatestTag(unittest.TestCase):
    """download_release_asset uses /releases/latest when tag is 'latest'."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_latest_tag_uses_releases_latest_endpoint(self) -> None:
        dest = Path(self._tmpdir.name)
        asset_name = "zlib-hyperlight-multi-process-128mb.tar.bz2"
        download_url = "https://example.com/" + asset_name
        file_content = b"archive-bytes"

        metadata_resp = _make_urlopen_response(
            _make_release_payload(asset_name, download_url)
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        captured_requests: list[object] = []

        def capture_urlopen(req: object, **kwargs: object) -> object:
            captured_requests.append(req)
            if len(captured_requests) == 1:
                return metadata_resp
            return file_resp

        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            github_mod.download_release_asset(
                repo="nanvix/zlib",
                tag="latest",
                asset_name=asset_name,
                dest=dest,
            )

        first_req = captured_requests[0]
        assert isinstance(first_req, urllib.request.Request)
        self.assertEqual(
            first_req.full_url,
            "https://api.github.com/repos/nanvix/zlib/releases/latest",
        )

    def test_non_latest_tag_uses_releases_tags_endpoint(self) -> None:
        dest = Path(self._tmpdir.name)
        asset_name = "zlib-hyperlight-multi-process-128mb.tar.bz2"
        download_url = "https://example.com/" + asset_name
        file_content = b"archive-bytes"

        metadata_resp = _make_urlopen_response(
            _make_release_payload(asset_name, download_url)
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        captured_requests: list[object] = []

        def capture_urlopen(req: object, **kwargs: object) -> object:
            captured_requests.append(req)
            if len(captured_requests) == 1:
                return metadata_resp
            return file_resp

        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            github_mod.download_release_asset(
                repo="nanvix/zlib",
                tag="abc1234-nanvix-def5678",
                asset_name=asset_name,
                dest=dest,
            )

        first_req = captured_requests[0]
        assert isinstance(first_req, urllib.request.Request)
        self.assertEqual(
            first_req.full_url,
            "https://api.github.com/repos/nanvix/zlib/releases/tags/abc1234-nanvix-def5678",
        )


class TestDownloadReleaseAssetPrefixMatch(unittest.TestCase):
    """download_release_asset with match_prefix=True."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_prefix_match_finds_asset_with_sha(self) -> None:
        dest = Path(self._tmpdir.name)
        prefix = "nanvix-hyperlight-multi-process-release-128mb"
        full_name = f"{prefix}-abc123.tar.bz2"
        download_url = f"https://example.com/{full_name}"
        file_content = b"archive-bytes"

        metadata_resp = _make_urlopen_response(
            _make_release_payload(full_name, download_url)
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        with patch("urllib.request.urlopen", side_effect=[metadata_resp, file_resp]):
            result = github_mod.download_release_asset(
                repo="nanvix/nanvix",
                tag="latest",
                asset_name=prefix,
                dest=dest,
                match_prefix=True,
            )

        self.assertTrue(result.exists())
        self.assertEqual(result.name, full_name)
        self.assertEqual(result.read_bytes(), file_content)

    def test_prefix_match_cache_hit(self) -> None:
        dest = Path(self._tmpdir.name)
        prefix = "nanvix-hyperlight-multi-process-release-128mb"
        cached = dest / f"{prefix}-def456.tar.bz2"
        cached.write_bytes(b"cached")

        with patch("urllib.request.urlopen") as mock_open:
            result = github_mod.download_release_asset(
                repo="nanvix/nanvix",
                tag="latest",
                asset_name=prefix,
                dest=dest,
                match_prefix=True,
            )
            mock_open.assert_not_called()

        self.assertEqual(result, cached)

    def test_prefix_match_no_match_exits_3(self) -> None:
        dest = Path(self._tmpdir.name)
        metadata_resp = _make_urlopen_response(
            _make_release_payload("unrelated-asset.tar.bz2", "https://example.com/x")
        )
        log_mod.set_json_mode(True)

        with patch("urllib.request.urlopen", return_value=metadata_resp):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.download_release_asset(
                    repo="nanvix/nanvix",
                    tag="latest",
                    asset_name="nanvix-hyperlight-multi-process-release-128mb",
                    dest=dest,
                    match_prefix=True,
                )
        self.assertEqual(ctx.exception.code, 3)


# ---------------------------------------------------------------------------
# find_release_tag
# ---------------------------------------------------------------------------


def _make_releases_list_payload(tags: list[str]) -> bytes:
    """Return a minimal GitHub ``GET /repos/{repo}/releases`` response."""
    return json.dumps([{"tag_name": t} for t in tags]).encode()


class TestFindReleaseTag(unittest.TestCase):
    """Tests for :func:`github.find_release_tag`."""

    def setUp(self) -> None:
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        log_mod.set_json_mode(False)

    def test_returns_matching_tag(self) -> None:
        tags = [
            "b7a6a3c-nanvix-fa06b88",
            "25e1341-nanvix-fa06b88",
            "bd416c7-nanvix-98c15d9",
        ]
        resp = _make_urlopen_response(_make_releases_list_payload(tags))

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod.find_release_tag("nanvix/zlib", "nanvix-fa06b88")

        self.assertEqual(result, "b7a6a3c-nanvix-fa06b88")

    def test_returns_none_when_no_match(self) -> None:
        tags = ["b7a6a3c-nanvix-fa06b88", "25e1341-nanvix-fa06b88"]
        resp = _make_urlopen_response(_make_releases_list_payload(tags))

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod.find_release_tag("nanvix/zlib", "nanvix-000000")

        self.assertIsNone(result)

    def test_returns_none_for_empty_releases(self) -> None:
        resp = _make_urlopen_response(json.dumps([]).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod.find_release_tag("nanvix/zlib", "nanvix-fa06b88")

        self.assertIsNone(result)

    def test_gh_token_passed_in_header(self) -> None:
        tags = ["abc-nanvix-def"]
        resp = _make_urlopen_response(_make_releases_list_payload(tags))

        captured: list[urllib.request.Request] = []

        def capture(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            captured.append(req)
            return resp

        with patch("urllib.request.urlopen", side_effect=capture):
            github_mod.find_release_tag("nanvix/zlib", "nanvix-def", gh_token="tok123")

        self.assertEqual(len(captured), 1)
        self.assertIn("Authorization", captured[0].headers)
        self.assertEqual(captured[0].headers["Authorization"], "Bearer tok123")

    def test_network_error_exits_4(self) -> None:
        log_mod.set_json_mode(True)

        with (
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("connection refused"),
            ),
            patch("time.sleep"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.find_release_tag("nanvix/zlib", "nanvix-fa06b88")

        self.assertEqual(ctx.exception.code, 4)


if __name__ == "__main__":
    unittest.main()
