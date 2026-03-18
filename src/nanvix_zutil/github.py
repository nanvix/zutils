# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""GitHub release artifact downloader.

Downloads release assets from the GitHub API with automatic retry and
exponential back-off.  Pass a ``GH_TOKEN`` (personal access token) to
avoid being rate-limited on public repositories.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

from nanvix_zutil import log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GITHUB_API_BASE = "https://api.github.com"
_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0  # seconds
_HTTP_TIMEOUT = 30.0  # seconds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def download_release_asset(
    repo: str,
    tag: str,
    asset_name: str,
    dest: Path,
    gh_token: str | None = None,
) -> Path:
    """Download a GitHub release asset to *dest*.

    The file is saved as ``dest / asset_name``.  If the file already exists
    it is returned immediately without re-downloading.

    Args:
        repo: Repository in ``owner/name`` format (e.g. ``"nanvix/zlib"``).
        tag: Release tag (e.g. ``"v1.2.3"``).
        asset_name: The exact file name of the release asset.
        dest: Directory where the asset will be written.
        gh_token: Optional GitHub personal access token.

    Returns:
        Path to the downloaded file.

    Raises:
        SystemExit: With exit code ``4`` on network failure after all retries.
    """
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / asset_name

    if out_path.exists():
        log.info(f"Asset already present: {out_path}")
        return out_path

    url = f"{_GITHUB_API_BASE}/repos/{repo}/releases/tags/{tag}"
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    asset_url: str | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                try:
                    raw: object = json.loads(resp.read())
                    if not isinstance(raw, dict):
                        log.fatal(
                            f"Unexpected response from GitHub API for {repo}@{tag}",
                            code=4,
                        )
                    release = cast(dict[str, object], raw)
                    raw_assets: object = release.get("assets", [])
                    if not isinstance(raw_assets, list):
                        log.fatal(
                            f"Unexpected assets format from GitHub API for {repo}@{tag}",
                            code=4,
                        )
                    assets = cast(list[object], raw_assets)
                    for item in assets:
                        if not isinstance(item, dict):
                            continue
                        asset = cast(dict[str, object], item)
                        if asset.get("name") == asset_name:
                            asset_url = str(asset["browser_download_url"])
                            break
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    log.fatal(
                        f"Failed to decode GitHub API response for {repo}@{tag}: {exc}",
                        code=4,
                        hint="GitHub returned a malformed or non-JSON response; this may indicate a transient outage or HTML error page.",
                    )
            break
        except urllib.error.URLError as exc:
            if attempt == _MAX_RETRIES:
                log.fatal(
                    f"Failed to fetch release metadata for {repo}@{tag}: {exc}",
                    code=4,
                    hint="Check your network connection or set GH_TOKEN.",
                )
            wait = _BACKOFF_BASE**attempt
            log.warning(f"Attempt {attempt} failed; retrying in {wait:.0f}s…")
            time.sleep(wait)

    if asset_url is None:
        log.fatal(
            f"Asset '{asset_name}' not found in release {repo}@{tag}",
            code=3,
            hint="Check the release tag and asset name.",
        )

    log.info(f"Downloading {asset_name} from {repo}@{tag}…")
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            dl_req = urllib.request.Request(asset_url, headers=headers)
            with (
                urllib.request.urlopen(dl_req, timeout=_HTTP_TIMEOUT) as resp,
                out_path.open("wb") as out_fh,
            ):
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out_fh.write(chunk)
            log.success(f"Downloaded {asset_name}")
            return out_path
        except urllib.error.URLError as exc:
            if attempt == _MAX_RETRIES:
                log.fatal(
                    f"Failed to download {asset_name}: {exc}",
                    code=4,
                    hint="Check your network connection or set GH_TOKEN.",
                )
            wait = _BACKOFF_BASE**attempt
            log.warning(f"Download attempt {attempt} failed; retrying in {wait:.0f}s…")
            time.sleep(wait)

    # Unreachable — log.fatal exits. Satisfy type checker.
    return out_path  # pragma: no cover
