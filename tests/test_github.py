# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# pyright: reportPrivateUsage=false

"""Tests for nanvix_zutil.github."""

import io
import json
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanvix_zutil.github as github_mod
import nanvix_zutil.log as log_mod
from nanvix_zutil.sysroot import Sysroot


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


def _make_tar_bz2(members: dict[str, bytes]) -> bytes:
    """Return a ``.tar.bz2`` archive containing the given *members*."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


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
                    version_specifier="v1.0.0",
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
        log_mod.set_json_mode(True)

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
# _list_releases (pagination)
# ---------------------------------------------------------------------------


class TestListReleases(unittest.TestCase):
    """Tests for :func:`github._list_releases` Link-header pagination."""

    _headers: dict[str, str] = {"Accept": "application/vnd.github+json"}

    def setUp(self) -> None:
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        log_mod.set_json_mode(False)

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

    def setUp(self) -> None:
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        log_mod.set_json_mode(False)

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

    def setUp(self) -> None:
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        log_mod.set_json_mode(False)

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

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = github_mod._resolve_release(
                "nanvix/nanvix", "1.2.3", self._headers, semver=True
            )

        assert result is not None
        self.assertEqual(result["tag_name"], "1.2.3")
        self.assertEqual(call_count, 2)

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

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = github_mod._resolve_release(
                "nanvix/nanvix", "0.12.257", self._headers, semver=True
            )

        assert result is not None
        self.assertEqual(result["name"], "Nanvix 0.12.257")

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

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with self.assertRaises(SystemExit) as ctx:
                github_mod._resolve_release(
                    "nanvix/nanvix", "9.9.9", self._headers, semver=True
                )

        self.assertEqual(ctx.exception.code, 3)


# ---------------------------------------------------------------------------
# download_release_asset with full hash resolution
# ---------------------------------------------------------------------------


class TestDownloadReleaseAssetFullHash(unittest.TestCase):
    """download_release_asset resolves full commit SHAs to releases."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

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
        log_mod.set_json_mode(True)

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

    def setUp(self) -> None:
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        log_mod.set_json_mode(False)

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

    def setUp(self) -> None:
        log_mod.set_json_mode(True)

    def tearDown(self) -> None:
        log_mod.set_json_mode(False)

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


# ---------------------------------------------------------------------------
# Sysroot commitish
# ---------------------------------------------------------------------------


class TestSysrootCommitish(unittest.TestCase):
    """Sysroot.download() stores the commitish from the resolved release."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        log_mod.set_json_mode(False)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        log_mod.set_json_mode(False)

    def test_commitish_set_on_fresh_download(self) -> None:
        dest = Path(self._tmpdir.name) / "sysroot"
        archive = _make_tar_bz2({})
        archive_path = Path(self._tmpdir.name) / "nanvix.tar.bz2"
        archive_path.write_bytes(archive)

        mock_release: dict[str, object] = {
            "target_commitish": "e63706b1234567890abcdef",
        }
        with (
            patch(
                "nanvix_zutil.github.resolve_release",
                return_value=mock_release,
            ),
            patch(
                "nanvix_zutil.github.download_release_asset",
                return_value=archive_path,
            ),
        ):
            sysroot = Sysroot.download(
                machine="hyperlight",
                deployment_mode="multi-process",
                memory_size="128mb",
                tag="0.12.257",
                dest=dest,
            )

        self.assertEqual(sysroot.commitish, "e63706b")

    def test_commitish_empty_when_cached(self) -> None:
        dest = Path(self._tmpdir.name) / "sysroot"
        dest.mkdir()

        sysroot = Sysroot.download(
            machine="hyperlight",
            deployment_mode="multi-process",
            memory_size="128mb",
            tag="0.12.257",
            dest=dest,
        )

        self.assertEqual(sysroot.commitish, "")


if __name__ == "__main__":
    unittest.main()
