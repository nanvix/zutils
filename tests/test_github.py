# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# pyright: reportPrivateUsage=false

"""Tests for nanvix_zutil.github."""

import collections.abc
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    resp.getheader.return_value = None
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

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_returns_existing_path_without_network(self) -> None:
        dest = Path(self._tmpdir.name)
        asset = dest / "my-asset.tar.bz2"
        asset.write_bytes(b"data")

        with patch("urllib.request.urlopen") as mock_open:
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                version_specifier="v1.0.0",
                asset_name="my-asset.tar.bz2",
                dest=dest,
            )
            mock_open.assert_not_called()

        self.assertEqual(result, asset)


class TestDownloadReleaseAssetSuccess(unittest.TestCase):
    """download_release_asset fetches the file on a cache miss."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

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
                version_specifier="v1.0.0",
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
                version_specifier="v1.0.0",
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

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_asset_not_in_release_exits_3(self) -> None:
        dest = Path(self._tmpdir.name)
        # Release has no matching asset.
        metadata_resp = _make_urlopen_response(json.dumps({"assets": []}).encode())

        with patch("urllib.request.urlopen", return_value=metadata_resp):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.download_release_asset(
                    repo="nanvix/zlib",
                    version_specifier="v1.0.0",
                    asset_name="missing.tar.bz2",
                    dest=dest,
                )
        self.assertEqual(ctx.exception.code, 3)


class TestDownloadReleaseAssetNetworkError(unittest.TestCase):
    """download_release_asset exits with code 4 after all retries fail."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

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
                    version_specifier="v1.0.0",
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
                    version_specifier="v1.0.0",
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
                    version_specifier="v1.0.0",
                    asset_name="asset.tar.bz2",
                    dest=dest,
                )
        self.assertEqual(ctx.exception.code, 4)


class TestDownloadReleaseAssetLatestTag(unittest.TestCase):
    """download_release_asset uses /releases/latest when tag is 'latest'."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

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
                version_specifier="latest",
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
                version_specifier="abc1234-nanvix-def5678",
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

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

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
                version_specifier="latest",
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
                version_specifier="latest",
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

        with patch("urllib.request.urlopen", return_value=metadata_resp):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.download_release_asset(
                    repo="nanvix/nanvix",
                    version_specifier="latest",
                    asset_name="nanvix-hyperlight-multi-process-release-128mb",
                    dest=dest,
                    match_prefix=True,
                )
        self.assertEqual(ctx.exception.code, 3)


class TestDownloadReleaseAssetPrefixPreference(unittest.TestCase):
    """Prefix matching picks .tar.bz2 over .tar.gz deterministically."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_multi_asset_release(self, assets: list[tuple[str, str]]) -> bytes:
        """Build a release payload with multiple assets."""
        return json.dumps(
            {
                "assets": [
                    {"name": name, "browser_download_url": url} for name, url in assets
                ]
            }
        ).encode()

    def test_prefers_tar_bz2_over_tar_gz(self) -> None:
        """When both .tar.bz2 and .tar.gz exist, .tar.bz2 is selected."""
        dest = Path(self._tmpdir.name)
        prefix = "zlib-microvm-standalone-256mb"
        bz2_name = f"{prefix}.tar.bz2"
        gz_name = f"{prefix}.tar.gz"
        file_content = b"bz2-content"

        metadata_resp = _make_urlopen_response(
            self._make_multi_asset_release(
                [
                    (gz_name, f"https://example.com/{gz_name}"),
                    (bz2_name, f"https://example.com/{bz2_name}"),
                ]
            )
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        with patch("urllib.request.urlopen", side_effect=[metadata_resp, file_resp]):
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                version_specifier="v1.0.0",
                asset_name=prefix,
                dest=dest,
                match_prefix=True,
            )

        self.assertEqual(result.name, bz2_name)

    def test_prefers_tar_bz2_regardless_of_api_order(self) -> None:
        """Preference holds even when .tar.bz2 appears first in the API."""
        dest = Path(self._tmpdir.name)
        prefix = "zlib-microvm-standalone-256mb"
        bz2_name = f"{prefix}.tar.bz2"
        gz_name = f"{prefix}.tar.gz"
        file_content = b"bz2-content"

        metadata_resp = _make_urlopen_response(
            self._make_multi_asset_release(
                [
                    (bz2_name, f"https://example.com/{bz2_name}"),
                    (gz_name, f"https://example.com/{gz_name}"),
                ]
            )
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        with patch("urllib.request.urlopen", side_effect=[metadata_resp, file_resp]):
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                version_specifier="v1.0.0",
                asset_name=prefix,
                dest=dest,
                match_prefix=True,
            )

        self.assertEqual(result.name, bz2_name)

    def test_falls_back_to_tar_gz_when_no_bz2(self) -> None:
        """When only .tar.gz matches the prefix, it is selected."""
        dest = Path(self._tmpdir.name)
        prefix = "zlib-microvm-standalone-256mb"
        gz_name = f"{prefix}.tar.gz"
        file_content = b"gz-content"

        metadata_resp = _make_urlopen_response(
            self._make_multi_asset_release(
                [
                    (gz_name, f"https://example.com/{gz_name}"),
                ]
            )
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        with patch("urllib.request.urlopen", side_effect=[metadata_resp, file_resp]):
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                version_specifier="v1.0.0",
                asset_name=prefix,
                dest=dest,
                match_prefix=True,
            )

        self.assertEqual(result.name, gz_name)

    def test_prefix_with_sha_prefers_bz2(self) -> None:
        """SHA-suffixed assets still prefer .tar.bz2."""
        dest = Path(self._tmpdir.name)
        prefix = "nanvix-hyperlight-multi-process-release-128mb"
        bz2_name = f"{prefix}-abc123.tar.bz2"
        gz_name = f"{prefix}-abc123.tar.gz"
        file_content = b"bz2-content"

        metadata_resp = _make_urlopen_response(
            self._make_multi_asset_release(
                [
                    (gz_name, f"https://example.com/{gz_name}"),
                    (bz2_name, f"https://example.com/{bz2_name}"),
                ]
            )
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        with patch("urllib.request.urlopen", side_effect=[metadata_resp, file_resp]):
            result = github_mod.download_release_asset(
                repo="nanvix/nanvix",
                version_specifier="latest",
                asset_name=prefix,
                dest=dest,
                match_prefix=True,
            )

        self.assertEqual(result.name, bz2_name)

    def test_prefers_tar_over_zip(self) -> None:
        """Tarballs are preferred over .zip."""
        dest = Path(self._tmpdir.name)
        prefix = "zlib-microvm-standalone-256mb"
        zip_name = f"{prefix}.zip"
        gz_name = f"{prefix}.tar.gz"
        file_content = b"gz-content"

        metadata_resp = _make_urlopen_response(
            self._make_multi_asset_release(
                [
                    (zip_name, f"https://example.com/{zip_name}"),
                    (gz_name, f"https://example.com/{gz_name}"),
                ]
            )
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        with patch("urllib.request.urlopen", side_effect=[metadata_resp, file_resp]):
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                version_specifier="v1.0.0",
                asset_name=prefix,
                dest=dest,
                match_prefix=True,
            )

        self.assertEqual(result.name, gz_name)

    def test_falls_back_to_zip_when_no_tarballs(self) -> None:
        """When only .zip matches the prefix, it is selected."""
        dest = Path(self._tmpdir.name)
        prefix = "zlib-microvm-standalone-256mb"
        zip_name = f"{prefix}.zip"
        file_content = b"zip-content"

        metadata_resp = _make_urlopen_response(
            self._make_multi_asset_release(
                [
                    (zip_name, f"https://example.com/{zip_name}"),
                ]
            )
        )
        file_resp = _make_urlopen_response(file_content, chunked=True)

        with patch("urllib.request.urlopen", side_effect=[metadata_resp, file_resp]):
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                version_specifier="v1.0.0",
                asset_name=prefix,
                dest=dest,
                match_prefix=True,
            )

        self.assertEqual(result.name, zip_name)


# ---------------------------------------------------------------------------
# _is_commit_hash
# ---------------------------------------------------------------------------


class TestIsCommitHash(unittest.TestCase):
    """Tests for :func:`github._is_commit_hash`."""

    def test_40_char_lowercase_hex(self) -> None:
        self.assertTrue(github_mod._is_commit_hash("a" * 40))

    def test_40_char_uppercase_hex(self) -> None:
        self.assertTrue(github_mod._is_commit_hash("A" * 40))

    def test_40_char_mixed_case(self) -> None:
        self.assertTrue(github_mod._is_commit_hash("0123456789abcdefABCD" * 2))

    def test_7_char_hex(self) -> None:
        self.assertTrue(github_mod._is_commit_hash("abc1234"))

    def test_4_char_hex(self) -> None:
        self.assertTrue(github_mod._is_commit_hash("abcd"))

    def test_39_char_hex(self) -> None:
        self.assertTrue(github_mod._is_commit_hash("a" * 39))

    def test_mixed_case_short(self) -> None:
        self.assertTrue(github_mod._is_commit_hash("AbCdEf1"))

    def test_3_chars_is_false(self) -> None:
        self.assertFalse(github_mod._is_commit_hash("abc"))

    def test_41_chars_is_false(self) -> None:
        self.assertFalse(github_mod._is_commit_hash("a" * 41))

    def test_non_hex_is_false(self) -> None:
        self.assertFalse(github_mod._is_commit_hash("ghijklm"))

    def test_empty_string_is_false(self) -> None:
        self.assertFalse(github_mod._is_commit_hash(""))


# ---------------------------------------------------------------------------
# _parse_next_link
# ---------------------------------------------------------------------------


class TestParseNextLink(unittest.TestCase):
    """Tests for :func:`github._parse_next_link`."""

    def test_returns_next_url(self) -> None:
        header = (
            '<https://api.github.com/repos/o/r/releases?per_page=100&page=2>; rel="next", '
            '<https://api.github.com/repos/o/r/releases?per_page=100&page=5>; rel="last"'
        )
        self.assertEqual(
            github_mod._parse_next_link(header),
            "https://api.github.com/repos/o/r/releases?per_page=100&page=2",
        )

    def test_returns_none_when_no_next(self) -> None:
        header = '<https://api.github.com/repos/o/r/releases?page=1>; rel="prev"'
        self.assertIsNone(github_mod._parse_next_link(header))

    def test_returns_none_for_none_input(self) -> None:
        self.assertIsNone(github_mod._parse_next_link(None))

    def test_returns_none_for_empty_string(self) -> None:
        self.assertIsNone(github_mod._parse_next_link(""))


# ---------------------------------------------------------------------------
# _fetch_json
# ---------------------------------------------------------------------------


class TestFetchJson(unittest.TestCase):
    """Tests for :func:`github._fetch_json` 404 handling."""

    _headers: dict[str, str] = {"Accept": "application/vnd.github+json"}

    def test_404_without_allow_404_exits_3(self) -> None:
        """HTTP 404 (allow_404=False) exits with EXIT_MISSING_DEP (3)."""
        url = "https://api.github.com/repos/nanvix/zlib/releases/tags/v0.0.0"

        def side_effect(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "Not Found",
                {},  # type: ignore[arg-type]
                None,
            )

        with (
            patch("urllib.request.urlopen", side_effect=side_effect),
            patch("time.sleep") as mock_sleep,
        ):
            with self.assertRaises(SystemExit) as ctx:
                github_mod._fetch_json(url, self._headers, "nanvix/zlib@v0.0.0")

        self.assertEqual(ctx.exception.code, 3)
        # 404 must short-circuit without exponential back-off.
        mock_sleep.assert_not_called()

    def test_404_with_allow_404_returns_none_without_backoff(self) -> None:
        """HTTP 404 with allow_404=True returns None without sleeping."""
        url = "https://api.github.com/repos/nanvix/zlib/releases/tags/v0.0.0"

        def side_effect(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "Not Found",
                {},  # type: ignore[arg-type]
                None,
            )

        with (
            patch("urllib.request.urlopen", side_effect=side_effect) as mock_urlopen,
            patch("time.sleep") as mock_sleep,
        ):
            result = github_mod._fetch_json(
                url, self._headers, "nanvix/zlib@v0.0.0", allow_404=True
            )

        self.assertIsNone(result)
        mock_sleep.assert_not_called()
        # No retries on 404.
        self.assertEqual(mock_urlopen.call_count, 1)


# ---------------------------------------------------------------------------
# _list_releases (pagination)
# ---------------------------------------------------------------------------


class TestListReleases(unittest.TestCase):
    """Tests for :func:`github._list_releases` Link-header pagination."""

    _headers: dict[str, str] = {"Accept": "application/vnd.github+json"}

    def test_single_page_no_link_header(self) -> None:
        releases_data: list[dict[str, object]] = [
            {"tag_name": "v1", "target_commitish": "a" * 40},
            {"tag_name": "v2", "target_commitish": "b" * 40},
        ]
        resp = _make_urlopen_response(json.dumps(releases_data).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            result = list(github_mod._list_releases("nanvix/nanvix", self._headers))

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["tag_name"], "v1")
        self.assertEqual(result[1]["tag_name"], "v2")

    def test_follows_link_header_across_pages(self) -> None:
        page1_data: list[dict[str, object]] = [
            {"tag_name": "v1", "target_commitish": "a" * 40},
        ]
        page2_data: list[dict[str, object]] = [
            {"tag_name": "v2", "target_commitish": "b" * 40},
        ]

        resp1 = _make_urlopen_response(json.dumps(page1_data).encode())
        # Simulate Link header pointing to page 2.
        resp1.__enter__.return_value.getheader.return_value = '<https://api.github.com/repos/nanvix/nanvix/releases?per_page=100&page=2>; rel="next"'
        resp2 = _make_urlopen_response(json.dumps(page2_data).encode())

        with patch("urllib.request.urlopen", side_effect=[resp1, resp2]):
            result = list(github_mod._list_releases("nanvix/nanvix", self._headers))

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["tag_name"], "v1")
        self.assertEqual(result[1]["tag_name"], "v2")

    def test_generator_short_circuits(self) -> None:
        """Caller breaking out of iteration does not fetch next page."""
        page1_data: list[dict[str, object]] = [
            {"tag_name": "v1", "target_commitish": "a" * 40},
        ]
        resp1 = _make_urlopen_response(json.dumps(page1_data).encode())
        resp1.__enter__.return_value.getheader.return_value = (
            '<https://api.github.com/repos/nanvix/nanvix/releases?page=2>; rel="next"'
        )

        with patch("urllib.request.urlopen", return_value=resp1) as mock_open:
            for release in github_mod._list_releases("nanvix/nanvix", self._headers):
                if release["tag_name"] == "v1":
                    break

        # Only one page was fetched despite a next link existing.
        mock_open.assert_called_once()


# ---------------------------------------------------------------------------
# _resolve_release
# ---------------------------------------------------------------------------


class TestResolveRelease(unittest.TestCase):
    """Tests for :func:`github._resolve_release` (full hash resolution)."""

    _headers: dict[str, str] = {"Accept": "application/vnd.github+json"}

    def test_latest_uses_releases_latest_endpoint(self) -> None:
        release_data: dict[str, object] = {"tag_name": "latest", "assets": []}
        resp = _make_urlopen_response(json.dumps(release_data).encode())

        captured: list[urllib.request.Request] = []

        def capture(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            captured.append(req)
            return resp

        with patch("urllib.request.urlopen", side_effect=capture):
            result = github_mod._resolve_release(
                "nanvix/nanvix", "latest", self._headers
            )

        assert result is not None
        self.assertEqual(result["tag_name"], "latest")
        self.assertIn("/releases/latest", captured[0].full_url)

    def test_full_hash_match_returns_release(self) -> None:
        sha = "a1b2c3d4e5f6" + "0" * 28  # 40-char hex
        releases_list: list[dict[str, object]] = [
            {"tag_name": "latest", "target_commitish": "f" * 40, "assets": []},
            {
                "tag_name": "latest",
                "target_commitish": sha,
                "assets": [{"name": "asset.tar.bz2"}],
            },
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod._resolve_release("nanvix/nanvix", sha, self._headers)

        assert result is not None
        self.assertEqual(result["target_commitish"], sha)

    def test_full_hash_no_match_exits_3(self) -> None:
        sha = "a" * 40
        releases_list: list[dict[str, object]] = [
            {"tag_name": "latest", "target_commitish": "b" * 40, "assets": []},
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(SystemExit) as ctx:
                github_mod._resolve_release("nanvix/nanvix", sha, self._headers)

        self.assertEqual(ctx.exception.code, 3)

    def test_plain_tag_uses_releases_tags_endpoint(self) -> None:
        release_data: dict[str, object] = {"tag_name": "v1.0.0", "assets": []}
        resp = _make_urlopen_response(json.dumps(release_data).encode())

        captured: list[urllib.request.Request] = []

        def capture(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            captured.append(req)
            return resp

        with patch("urllib.request.urlopen", side_effect=capture):
            result = github_mod._resolve_release("nanvix/zlib", "v1.0.0", self._headers)

        assert result is not None
        self.assertEqual(result["tag_name"], "v1.0.0")
        self.assertIn("/releases/tags/v1.0.0", captured[0].full_url)

    def test_short_hash_match_returns_release(self) -> None:
        full_sha = "a1b2c3d4e5f6" + "0" * 28
        short = "a1b2c3d"
        releases_list: list[dict[str, object]] = [
            {"tag_name": "latest", "target_commitish": "f" * 40, "assets": []},
            {
                "tag_name": "latest",
                "target_commitish": full_sha,
                "assets": [{"name": "asset.tar.bz2"}],
            },
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod._resolve_release("nanvix/nanvix", short, self._headers)

        assert result is not None
        self.assertEqual(result["target_commitish"], full_sha)

    def test_short_hash_ambiguous_returns_first(self) -> None:
        releases_list: list[dict[str, object]] = [
            {"tag_name": "r1", "target_commitish": "abc1234" + "0" * 33},
            {"tag_name": "r2", "target_commitish": "abc1234" + "1" * 33},
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod._resolve_release(
                "nanvix/nanvix", "abc1234", self._headers
            )

        assert result is not None
        self.assertEqual(result["tag_name"], "r1")

    def test_short_hash_no_match_exits_3(self) -> None:
        releases_list: list[dict[str, object]] = [
            {"tag_name": "latest", "target_commitish": "f" * 40, "assets": []},
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(SystemExit) as ctx:
                github_mod._resolve_release("nanvix/nanvix", "abc1234", self._headers)

        self.assertEqual(ctx.exception.code, 3)

    def test_short_hash_resolves_hello_world_example(self) -> None:
        """The 7-char prefix from hello-world's nanvix.toml resolves."""
        full_sha = "e4172c1b6b8ed69209c358b94641b25dd6f2d4e1"
        short_hash = "e4172c1"
        releases_list: list[dict[str, object]] = [
            {
                "tag_name": "latest",
                "target_commitish": full_sha,
                "assets": [
                    {
                        "name": "nanvix-hyperlight-multi-process-release-128mb.tar.bz2",
                        "browser_download_url": "https://github.com/nanvix/nanvix/releases/download/latest/nanvix-hyperlight-multi-process-release-128mb.tar.bz2",
                    }
                ],
            },
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod._resolve_release(
                "nanvix/nanvix", short_hash, self._headers
            )

        assert result is not None
        self.assertEqual(result["target_commitish"], full_sha)
        self.assertEqual(result["tag_name"], "latest")

    def test_full_hash_case_insensitive(self) -> None:
        """Uppercase user input matches lowercase API commitish."""
        lower_sha = "a1b2c3d4e5f6" + "0" * 28
        upper_sha = lower_sha.upper()
        releases_list: list[dict[str, object]] = [
            {
                "tag_name": "latest",
                "target_commitish": lower_sha,
                "assets": [],
            },
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod._resolve_release(
                "nanvix/nanvix", upper_sha, self._headers
            )

        assert result is not None
        self.assertEqual(result["target_commitish"], lower_sha)

    def test_short_hash_case_insensitive(self) -> None:
        """Mixed-case short hash matches lowercase API commitish."""
        full_sha = "a1b2c3d4e5f6" + "0" * 28
        mixed_short = "A1B2c3D"
        releases_list: list[dict[str, object]] = [
            {
                "tag_name": "latest",
                "target_commitish": full_sha,
                "assets": [],
            },
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            result = github_mod._resolve_release(
                "nanvix/nanvix", mixed_short, self._headers
            )

        assert result is not None
        self.assertEqual(result["target_commitish"], full_sha)


# ---------------------------------------------------------------------------
# _is_semver
# ---------------------------------------------------------------------------


class TestIsSemver(unittest.TestCase):
    """Tests for :func:`github._is_semver`."""

    def test_valid_semver(self) -> None:
        self.assertTrue(github_mod._is_semver("1.2.3"))

    def test_large_numbers(self) -> None:
        self.assertTrue(github_mod._is_semver("0.12.257"))

    def test_zeros(self) -> None:
        self.assertTrue(github_mod._is_semver("0.0.0"))

    def test_v_prefix_is_false(self) -> None:
        self.assertFalse(github_mod._is_semver("v1.2.3"))

    def test_two_parts_is_false(self) -> None:
        self.assertFalse(github_mod._is_semver("1.2"))

    def test_four_parts_is_false(self) -> None:
        self.assertFalse(github_mod._is_semver("1.2.3.4"))

    def test_prerelease_is_false(self) -> None:
        self.assertFalse(github_mod._is_semver("1.2.3-beta"))

    def test_empty_string_is_false(self) -> None:
        self.assertFalse(github_mod._is_semver(""))

    def test_non_numeric_is_false(self) -> None:
        self.assertFalse(github_mod._is_semver("a.b.c"))


# ---------------------------------------------------------------------------
# _resolve_release — semver resolution
# ---------------------------------------------------------------------------


class TestResolveReleaseSemver(unittest.TestCase):
    """Tests for semver resolution in :func:`github._resolve_release`."""

    _headers: dict[str, str] = {"Accept": "application/vnd.github+json"}

    def test_semver_resolves_via_v_prefix_tag(self) -> None:
        """``"1.2.3"`` resolves via ``GET /releases/tags/v1.2.3``."""
        release_data: dict[str, object] = {"tag_name": "v1.2.3", "assets": []}
        resp = _make_urlopen_response(json.dumps(release_data).encode())

        captured: list[urllib.request.Request] = []

        def capture(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            captured.append(req)
            return resp

        with patch("urllib.request.urlopen", side_effect=capture):
            result = github_mod._resolve_release(
                "nanvix/nanvix", "1.2.3", self._headers, semver=True
            )

        assert result is not None
        self.assertEqual(result["tag_name"], "v1.2.3")
        self.assertIn("/releases/tags/v1.2.3", captured[0].full_url)

    def test_semver_falls_back_to_bare_tag(self) -> None:
        """When ``v1.2.3`` is 404, tries ``1.2.3`` bare tag."""
        release_data: dict[str, object] = {"tag_name": "1.2.3", "assets": []}
        resp = _make_urlopen_response(json.dumps(release_data).encode())

        call_count = 0

        def side_effect(req: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            assert isinstance(req, urllib.request.Request)
            if call_count == 1:
                # v1.2.3 → 404
                raise urllib.error.HTTPError(
                    req.full_url,
                    404,
                    "Not Found",
                    {},  # type: ignore[arg-type]
                    None,
                )
            return resp

        with (
            patch("urllib.request.urlopen", side_effect=side_effect),
            patch("time.sleep") as mock_sleep,
        ):
            result = github_mod._resolve_release(
                "nanvix/nanvix", "1.2.3", self._headers, semver=True
            )

        assert result is not None
        self.assertEqual(result["tag_name"], "1.2.3")
        self.assertEqual(call_count, 2)
        # No exponential back-off on 404.
        mock_sleep.assert_not_called()

    def test_semver_falls_back_to_name_search(self) -> None:
        """When both tags 404, searches release names."""
        releases_list: list[dict[str, object]] = [
            {"tag_name": "latest", "name": "Nanvix 0.12.257", "assets": []},
        ]
        releases_resp = _make_urlopen_response(json.dumps(releases_list).encode())

        call_count = 0

        def side_effect(req: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            assert isinstance(req, urllib.request.Request)
            if "/releases/tags/" in req.full_url:
                raise urllib.error.HTTPError(
                    req.full_url,
                    404,
                    "Not Found",
                    {},  # type: ignore[arg-type]
                    None,
                )
            return releases_resp

        with (
            patch("urllib.request.urlopen", side_effect=side_effect),
            patch("time.sleep") as mock_sleep,
        ):
            result = github_mod._resolve_release(
                "nanvix/nanvix", "0.12.257", self._headers, semver=True
            )

        assert result is not None
        self.assertEqual(result["name"], "Nanvix 0.12.257")
        # No exponential back-off on 404.
        mock_sleep.assert_not_called()

    def test_semver_no_match_exits_3(self) -> None:
        """When no tag or name matches, exits with code 3."""
        releases_list: list[dict[str, object]] = [
            {"tag_name": "latest", "name": "Some Other Release", "assets": []},
        ]
        releases_resp = _make_urlopen_response(json.dumps(releases_list).encode())

        def side_effect(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            if "/releases/tags/" in req.full_url:
                raise urllib.error.HTTPError(
                    req.full_url,
                    404,
                    "Not Found",
                    {},  # type: ignore[arg-type]
                    None,
                )
            return releases_resp

        with (
            patch("urllib.request.urlopen", side_effect=side_effect),
            patch("time.sleep") as mock_sleep,
        ):
            with self.assertRaises(SystemExit) as ctx:
                github_mod._resolve_release(
                    "nanvix/nanvix", "9.9.9", self._headers, semver=True
                )

        self.assertEqual(ctx.exception.code, 3)
        # No exponential back-off on 404.
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# download_release_asset with full hash resolution
# ---------------------------------------------------------------------------


class TestDownloadReleaseAssetFullHash(unittest.TestCase):
    """download_release_asset resolves full commit SHAs to releases."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_full_hash_downloads_correct_asset(self) -> None:
        dest = Path(self._tmpdir.name)
        sha = "a1b2c3d4e5" + "0" * 30  # 40-char hex
        asset_name = "zlib-hyperlight-multi-process-128mb.tar.bz2"
        download_url = f"https://example.com/{asset_name}"
        file_content = b"archive-bytes"

        releases_list: list[dict[str, object]] = [
            {
                "tag_name": "latest",
                "target_commitish": sha,
                "assets": [
                    {
                        "name": asset_name,
                        "browser_download_url": download_url,
                    }
                ],
            },
        ]
        releases_resp = _make_urlopen_response(json.dumps(releases_list).encode())
        file_resp = _make_urlopen_response(file_content, chunked=True)

        with patch(
            "urllib.request.urlopen",
            side_effect=[releases_resp, file_resp],
        ):
            result = github_mod.download_release_asset(
                repo="nanvix/zlib",
                version_specifier=sha,
                asset_name=asset_name,
                dest=dest,
            )

        self.assertTrue(result.exists())
        self.assertEqual(result.read_bytes(), file_content)

    def test_full_hash_no_release_exits_3(self) -> None:
        dest = Path(self._tmpdir.name)
        sha = "a" * 40
        releases_list: list[dict[str, object]] = [
            {"tag_name": "latest", "target_commitish": "b" * 40, "assets": []},
        ]
        resp = _make_urlopen_response(json.dumps(releases_list).encode())

        with patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(SystemExit) as ctx:
                github_mod.download_release_asset(
                    repo="nanvix/zlib",
                    version_specifier=sha,
                    asset_name="asset.tar.bz2",
                    dest=dest,
                )

        self.assertEqual(ctx.exception.code, 3)


# ---------------------------------------------------------------------------
# _resolve_release — ID resolution
# ---------------------------------------------------------------------------


class TestResolveReleaseId(unittest.TestCase):
    """Tests for integer release ID resolution."""

    _headers: dict[str, str] = {"Accept": "application/vnd.github+json"}

    def test_int_tag_fetches_releases_id_endpoint(self) -> None:
        release_data: dict[str, object] = {"id": 12345678, "assets": []}
        resp = _make_urlopen_response(json.dumps(release_data).encode())

        captured: list[urllib.request.Request] = []

        def capture(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            captured.append(req)
            return resp

        with patch("urllib.request.urlopen", side_effect=capture):
            result = github_mod._resolve_release("nanvix/zlib", 12345678, self._headers)

        assert result is not None
        self.assertEqual(result["id"], 12345678)
        self.assertIn("/releases/12345678", captured[0].full_url)
        self.assertNotIn("/tags/", captured[0].full_url)


# ---------------------------------------------------------------------------
# _resolve_release — semver gating
# ---------------------------------------------------------------------------


class TestResolveReleaseSemverGating(unittest.TestCase):
    """Semver cascade only runs when ``semver=True``."""

    _headers: dict[str, str] = {"Accept": "application/vnd.github+json"}

    def test_semver_string_without_flag_uses_plain_tag(self) -> None:
        """``1.2.3`` without ``semver=True`` goes to /releases/tags/1.2.3."""
        release_data: dict[str, object] = {"tag_name": "1.2.3", "assets": []}
        resp = _make_urlopen_response(json.dumps(release_data).encode())

        captured: list[urllib.request.Request] = []

        def capture(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            captured.append(req)
            return resp

        with patch("urllib.request.urlopen", side_effect=capture):
            result = github_mod._resolve_release("nanvix/zlib", "1.2.3", self._headers)

        assert result is not None
        self.assertEqual(result["tag_name"], "1.2.3")
        self.assertIn("/releases/tags/1.2.3", captured[0].full_url)
        self.assertNotIn("/releases/tags/v1.2.3", captured[0].full_url)

    def test_semver_string_with_flag_tries_v_prefix(self) -> None:
        """``1.2.3`` with ``semver=True`` tries /releases/tags/v1.2.3 first."""
        release_data: dict[str, object] = {"tag_name": "v1.2.3", "assets": []}
        resp = _make_urlopen_response(json.dumps(release_data).encode())

        captured: list[urllib.request.Request] = []

        def capture(req: object, **kwargs: object) -> object:
            assert isinstance(req, urllib.request.Request)
            captured.append(req)
            return resp

        with patch("urllib.request.urlopen", side_effect=capture):
            result = github_mod._resolve_release(
                "nanvix/zlib", "1.2.3", self._headers, semver=True
            )

        assert result is not None
        self.assertEqual(result["tag_name"], "v1.2.3")
        self.assertIn("/releases/tags/v1.2.3", captured[0].full_url)


class TestFindBestRelease(unittest.TestCase):
    """Tests for _find_best_release()."""

    def test_finds_matching_release(self) -> None:
        """First release whose tag matches the prefix is returned."""
        releases = [
            {"tag_name": "1.3.1-nanvix-0.12.320"},
            {"tag_name": "1.3.1-nanvix-0.12.291"},
            {"tag_name": "1.2.0-nanvix-0.12.291"},
        ]
        with patch.object(github_mod, "_list_releases", return_value=iter(releases)):
            result = github_mod._find_best_release("nanvix/zlib", "1.3.1-nanvix-", {})
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["tag_name"], "1.3.1-nanvix-0.12.320")

    def test_returns_none_when_no_match(self) -> None:
        """Returns None when no tags match the prefix."""
        releases = [
            {"tag_name": "2.0.0-nanvix-0.12.320"},
            {"tag_name": "1.2.0-nanvix-0.12.291"},
        ]
        with patch.object(github_mod, "_list_releases", return_value=iter(releases)):
            result = github_mod._find_best_release("nanvix/zlib", "1.3.1-nanvix-", {})
        self.assertIsNone(result)

    def test_short_circuits_on_first_match(self) -> None:
        """Generator stops iterating after the first match."""
        call_count = 0

        def counting_gen(
            repo: str, headers: dict[str, str]
        ) -> collections.abc.Generator[dict[str, object], None, None]:
            nonlocal call_count
            items: list[dict[str, object]] = [
                {"tag_name": "1.3.1-nanvix-0.12.320"},
                {"tag_name": "1.3.1-nanvix-0.12.291"},
            ]
            for rel in items:
                call_count += 1
                yield rel

        with patch.object(github_mod, "_list_releases", side_effect=counting_gen):
            result = github_mod._find_best_release("nanvix/zlib", "1.3.1-nanvix-", {})
        self.assertIsNotNone(result)
        # The generator yielded 1 item and then the function returned.
        # Python generators only advance when next() is called, and
        # _find_best_release returns on the first match before calling
        # next() again.  So call_count should be 1.
        self.assertEqual(call_count, 1)


class TestResolveReleaseWithFallback(unittest.TestCase):
    """Tests for resolve_release_with_fallback()."""

    @patch.object(github_mod, "_find_best_release")
    @patch.object(github_mod, "_resolve_release")
    def test_exact_tag_found_no_fallback(
        self, mock_resolve: MagicMock, mock_best: MagicMock
    ) -> None:
        """When the exact tag exists, returns (release, None)."""
        release: dict[str, object] = {"tag_name": "1.3.1-nanvix-0.12.337"}
        mock_resolve.return_value = release

        result, fallback_ver = github_mod.resolve_release_with_fallback(
            "nanvix/zlib", "1.3.1-nanvix-0.12.337", "1.3.1"
        )
        self.assertEqual(result, release)
        self.assertIsNone(fallback_ver)
        mock_best.assert_not_called()

    @patch.object(github_mod, "_find_best_release")
    @patch.object(github_mod, "_resolve_release")
    def test_404_triggers_fallback(
        self, mock_resolve: MagicMock, mock_best: MagicMock
    ) -> None:
        """When exact tag 404s, scans releases and returns fallback."""
        mock_resolve.return_value = None  # allow_missing=True returns None
        fallback_release: dict[str, object] = {"tag_name": "1.3.1-nanvix-0.12.291"}
        mock_best.return_value = fallback_release

        result, fallback_ver = github_mod.resolve_release_with_fallback(
            "nanvix/zlib", "1.3.1-nanvix-0.12.337", "1.3.1"
        )
        self.assertEqual(result, fallback_release)
        self.assertEqual(fallback_ver, "0.12.291")

    @patch.object(github_mod, "_find_best_release")
    @patch.object(github_mod, "_resolve_release")
    def test_no_fallback_release_exits(
        self, mock_resolve: MagicMock, mock_best: MagicMock
    ) -> None:
        """When both exact and scan fail, exits with code 3."""
        mock_resolve.return_value = None
        mock_best.return_value = None

        with self.assertRaises(SystemExit) as ctx:
            github_mod.resolve_release_with_fallback(
                "nanvix/zlib", "1.3.1-nanvix-0.12.337", "1.3.1"
            )
        self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
