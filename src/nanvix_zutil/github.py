# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""GitHub release artifact downloader.

Downloads release assets from the GitHub API with automatic retry and
exponential back-off.  Pass a ``GH_TOKEN`` (personal access token) to
avoid being rate-limited on public repositories.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, cast, overload

from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP, EXIT_NETWORK_ERROR
from nanvix_zutil.utils import SEMVER_RE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GITHUB_API_BASE = "https://api.github.com"
_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0  # seconds
_HTTP_TIMEOUT = 30.0  # seconds
_PER_PAGE = 100  # releases per API page


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# NOTE: Short hex-only strings (4-6 chars) may be ambiguous with tag
# names.  Consider raising the minimum to 7 if this causes issues.
def _is_commit_hash(s: str) -> bool:
    """Return ``True`` if *s* is a 4–40 character hexadecimal string.

    Git resolves prefixes as short as 4 characters; full SHAs are 40.
    """
    return 4 <= len(s) <= 40 and all(c in "0123456789abcdefABCDEF" for c in s)


def _is_semver(s: str) -> bool:
    """Return ``True`` if *s* matches ``MAJOR.MINOR.PATCH`` digit pattern."""
    return SEMVER_RE.match(s) is not None


def _parse_next_link(link_header: str | None) -> str | None:
    """Extract the ``next`` URL from a GitHub ``Link`` response header.

    Args:
        link_header: The raw ``Link`` header value, or ``None``.

    Returns:
        The URL for the next page, or ``None`` if there is no next page.
    """
    if link_header is None:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            match = re.search(r"<([^>]+)>", part)
            if match:
                return match.group(1)
    return None


def _fetch_json(
    url: str,
    headers: dict[str, str],
    context: str,
    *,
    allow_404: bool = False,
) -> object | None:
    """Fetch JSON from *url* with retry and exponential back-off.

    Args:
        url: The URL to fetch.
        headers: HTTP headers.
        context: Human-readable context for error messages
            (e.g. ``"nanvix/zlib@v1.0.0"``).
        allow_404: If ``True``, return ``None`` on HTTP 404 instead of
            retrying or calling :func:`log.fatal`.

    Returns:
        The parsed JSON value, or ``None`` when *allow_404* is ``True``
        and the server responds with 404.

    Raises:
        SystemExit: On network or decode failure after all retries.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                try:
                    return json.loads(resp.read())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    log.fatal(
                        f"Failed to decode GitHub API response for {context}: {exc}",
                        code=EXIT_NETWORK_ERROR,
                        hint="GitHub returned a malformed or non-JSON response.",
                    )
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            if attempt == _MAX_RETRIES:
                log.fatal(
                    f"Failed to fetch {context}: {exc}",
                    code=EXIT_NETWORK_ERROR,
                    hint="Check your network connection or set GH_TOKEN.",
                )
            wait = _BACKOFF_BASE**attempt
            log.warning(f"Attempt {attempt} failed; retrying in {wait:.0f}s…")
            time.sleep(wait)
        except urllib.error.URLError as exc:
            if attempt == _MAX_RETRIES:
                log.fatal(
                    f"Failed to fetch {context}: {exc}",
                    code=EXIT_NETWORK_ERROR,
                    hint="Check your network connection or set GH_TOKEN.",
                )
            wait = _BACKOFF_BASE**attempt
            log.warning(f"Attempt {attempt} failed; retrying in {wait:.0f}s…")
            time.sleep(wait)

    raise SystemExit(EXIT_NETWORK_ERROR)  # pragma: no cover


@overload
def _fetch_release_by_url(
    repo: str,
    version_specifier: str | int,
    url: str,
    headers: dict[str, str],
) -> dict[str, object]: ...


@overload
def _fetch_release_by_url(
    repo: str,
    version_specifier: str | int,
    url: str,
    headers: dict[str, str],
    *,
    allow_404: Literal[True],
) -> dict[str, object] | None: ...


def _fetch_release_by_url(
    repo: str,
    version_specifier: str | int,
    url: str,
    headers: dict[str, str],
    *,
    allow_404: bool = False,
) -> dict[str, object] | None:
    """Fetch a single release by *url* and validate its shape.

    Args:
        repo: Repository in ``owner/name`` format (for error messages).
        version_specifier: Version specifier (for error messages).
        url: The GitHub API URL to fetch.
        headers: HTTP headers.
        allow_404: If ``True``, return ``None`` on HTTP 404.

    Returns:
        The release metadata dictionary, or ``None`` when *allow_404*
        is ``True`` and the server responds with 404.

    Raises:
        SystemExit: With exit code ``4`` on network failure or unexpected
            response format.
    """
    raw = _fetch_json(url, headers, f"{repo}@{version_specifier}", allow_404=allow_404)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        log.fatal(
            f"Unexpected response from GitHub API for {repo}@{version_specifier}",
            code=EXIT_NETWORK_ERROR,
        )
    return cast(dict[str, object], raw)


def _try_fetch_release_by_tag(
    repo: str,
    tag: str,
    headers: dict[str, str],
) -> dict[str, object] | None:
    """Try to fetch a release by tag name, returning ``None`` on 404.

    Args:
        repo: Repository in ``owner/name`` format.
        tag: Tag name to look up (e.g. ``"v1.0.0"``).
        headers: HTTP headers.

    Returns:
        The release metadata dictionary, or ``None`` if the tag does not
        exist (HTTP 404).

    Raises:
        SystemExit: With exit code ``4`` on non-404 network failure.
    """
    url = f"{_GITHUB_API_BASE}/repos/{repo}/releases/tags/{tag}"
    return _fetch_release_by_url(repo, tag, url, headers, allow_404=True)


def _find_best_release(
    repo: str,
    tag_prefix: str,
    headers: dict[str, str],
) -> dict[str, object] | None:
    """Find the newest release whose tag starts with *tag_prefix*.

    Scans releases in reverse-chronological order (GitHub API default)
    and returns the first match. Because GitHub lists releases newest
    first, the first match is the most recent.

    Args:
        repo: Repository in ``owner/name`` format.
        tag_prefix: Tag prefix to match (e.g. ``"1.3.1-nanvix-"``).
        headers: HTTP headers.

    Returns:
        The release metadata dictionary, or ``None`` if no match.
    """
    for release in _list_releases(repo, headers):
        tag = release.get("tag_name")
        if isinstance(tag, str) and tag.startswith(tag_prefix):
            return release
    return None


def _list_releases(
    repo: str,
    headers: dict[str, str],
) -> Iterator[dict[str, object]]:
    """Yield releases for *repo*, following ``Link``-header pagination.

    Fetches ``GET /repos/{repo}/releases`` with ``per_page=100`` and
    follows the ``rel="next"`` link until no more pages remain.  Yields
    individual release dictionaries so callers can short-circuit without
    fetching unnecessary pages.

    Args:
        repo: Repository in ``owner/name`` format.
        headers: HTTP headers (including optional auth token).

    Yields:
        Release dictionaries from the GitHub API.

    Raises:
        SystemExit: With exit code ``4`` on network failure after retries.
    """
    url: str | None = f"{_GITHUB_API_BASE}/repos/{repo}/releases?per_page={_PER_PAGE}"
    page = 0
    while url is not None:
        page += 1
        next_url: str | None = None
        page_items: list[object] = []
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                    try:
                        raw: object = json.loads(resp.read())
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        log.fatal(
                            f"Failed to decode GitHub API response for "
                            f"{repo} releases (page {page}): {exc}",
                            code=EXIT_NETWORK_ERROR,
                            hint="GitHub returned a malformed or non-JSON response.",
                        )
                    if not isinstance(raw, list):
                        log.fatal(
                            f"Unexpected response from GitHub API for {repo} releases",
                            code=EXIT_NETWORK_ERROR,
                        )
                    page_items = cast(list[object], raw)
                    next_url = _parse_next_link(resp.getheader("Link"))
                break
            except urllib.error.URLError as exc:
                if attempt == _MAX_RETRIES:
                    log.fatal(
                        f"Failed to fetch {repo} releases (page {page}): {exc}",
                        code=EXIT_NETWORK_ERROR,
                        hint="Check your network connection or set GH_TOKEN.",
                    )
                wait = _BACKOFF_BASE**attempt
                log.warning(f"Attempt {attempt} failed; retrying in {wait:.0f}s…")
                time.sleep(wait)

        if not page_items:
            break
        for item in page_items:
            if isinstance(item, dict):
                yield cast(dict[str, object], item)
        url = next_url


def _resolve_release(
    repo: str,
    version_specifier: str | int,
    headers: dict[str, str],
    *,
    semver: bool = False,
    allow_missing: bool = False,
) -> dict[str, object] | None:
    """Resolve a version spec to a GitHub release.

    Resolution strategies (checked in order):

    1. Integer — fetches ``/releases/{id}`` directly.
    2. ``"latest"`` — fetches ``/releases/latest``.
    3. Commit hash (4–40 hex characters) — searches releases by
       ``target_commitish``.
    4. Semver ``MAJOR.MINOR.PATCH`` (only when *semver* is ``True``) —
       tries ``v``-prefixed tag, bare tag, then release name search.
    5. Any other string — fetches ``/releases/tags/{tag}`` directly.

    Args:
        repo: Repository in ``owner/name`` format.
        version_specifier: Version specifier (tag name, ``"latest"``, commit hash,
            semver string, or integer release ID).
        headers: HTTP headers (including optional auth token).
        semver: When ``True``, enable the semver cascade for
            ``MAJOR.MINOR.PATCH`` strings.
        allow_missing: When ``True``, return ``None`` instead of calling
            :func:`log.fatal` when the release is not found.

    Returns:
        The release metadata dictionary, or ``None`` when *allow_missing*
        is ``True`` and no matching release is found.

    Raises:
        SystemExit: If no matching release is found (when *allow_missing*
            is ``False``) or on network failure.
    """
    vs = version_specifier
    if isinstance(vs, int):
        url = f"{_GITHUB_API_BASE}/repos/{repo}/releases/{vs}"
        if allow_missing:
            return _fetch_release_by_url(repo, vs, url, headers, allow_404=True)
        return _fetch_release_by_url(repo, vs, url, headers)

    if vs == "latest":
        url = f"{_GITHUB_API_BASE}/repos/{repo}/releases/latest"
        if allow_missing:
            return _fetch_release_by_url(repo, vs, url, headers, allow_404=True)
        return _fetch_release_by_url(repo, vs, url, headers)

    if _is_commit_hash(vs):
        needle = vs.lower()
        is_full = len(needle) == 40
        for release in _list_releases(repo, headers):
            commitish = release.get("target_commitish")
            if not isinstance(commitish, str):
                continue
            commitish_lower = commitish.lower()
            if is_full and commitish_lower == needle:
                return release
            if not is_full and commitish_lower.startswith(needle):
                return release
        if allow_missing:
            return None
        log.fatal(
            f"No release found for commit {vs} in {repo}",
            code=EXIT_MISSING_DEP,
            hint="Ensure a GitHub release exists whose target_commitish "
            "matches this commit SHA.",
        )

    if semver and _is_semver(vs):
        # 1. Try v-prefixed tag (e.g. "v1.2.3").
        result = _try_fetch_release_by_tag(repo, f"v{vs}", headers)
        if result is not None:
            return result
        # 2. Try bare tag (e.g. "1.2.3").
        result = _try_fetch_release_by_tag(repo, vs, headers)
        if result is not None:
            return result
        # 3. Search release names.
        for release in _list_releases(repo, headers):
            name = release.get("name")
            if isinstance(name, str) and vs in name:
                return release
        if allow_missing:
            return None
        log.fatal(
            f"No release found for version {vs} in {repo}",
            code=EXIT_MISSING_DEP,
            hint="Ensure a GitHub release exists with a tag like "
            f"'v{vs}' or '{vs}', or a release name containing '{vs}'.",
        )

    url = f"{_GITHUB_API_BASE}/repos/{repo}/releases/tags/{vs}"
    if allow_missing:
        return _try_fetch_release_by_tag(repo, vs, headers)
    return _fetch_release_by_url(repo, vs, url, headers)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_release(
    repo: str,
    version_specifier: str | int,
    gh_token: str | None = None,
    *,
    semver: bool = False,
) -> dict[str, object]:
    """Resolve a version spec to a GitHub release.

    Public wrapper around the internal resolution logic.  See
    :func:`download_release_asset` for the supported resolution
    strategies.

    Args:
        repo: Repository in ``owner/name`` format.
        version_specifier: Version specifier (tag name, ``"latest"``,
            commit hash, semver string, or integer release ID).
        gh_token: Optional GitHub personal access token.
        semver: When ``True``, enable the semver cascade for
            ``MAJOR.MINOR.PATCH`` strings.

    Returns:
        The release metadata dictionary.

    Raises:
        SystemExit: If no matching release is found or on network failure.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    result = _resolve_release(repo, version_specifier, headers, semver=semver)
    assert result is not None  # allow_missing defaults to False
    return result


def resolve_release_with_fallback(
    repo: str,
    version_specifier: str,
    base_version: str,
    gh_token: str | None = None,
) -> tuple[dict[str, object], str | None]:
    """Resolve a release, falling back to the best available on 404.

    Tries to fetch the release by exact tag. If the tag does not exist
    (HTTP 404), scans the repo's releases for the newest tag matching
    ``{base_version}-nanvix-*``.

    Args:
        repo: Repository in ``owner/name`` format.
        version_specifier: Exact tag to try first
            (e.g. ``"1.3.1-nanvix-0.12.337"``).
        base_version: The base package version without nanvix suffix
            (e.g. ``"1.3.1"``). Used to construct the fallback prefix.
        gh_token: Optional GitHub personal access token.

    Returns:
        A tuple of (release_dict, fallback_nanvix_version). The second
        element is ``None`` when the exact tag was found, or the nanvix
        version string from the fallback tag (e.g. ``"0.12.291"``) when
        a fallback was used.

    Raises:
        SystemExit: If no matching release is found at all.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    # Try exact tag first.
    result = _resolve_release(repo, version_specifier, headers, allow_missing=True)
    if result is not None:
        return result, None

    # Fallback: scan for best available release matching the base version.
    prefix = f"{base_version}-nanvix-"
    best = _find_best_release(repo, prefix, headers)
    if best is None:
        log.fatal(
            f"No release found for {repo}@{version_specifier} and no "
            f"fallback release matching '{prefix}*' exists",
            code=EXIT_MISSING_DEP,
            hint=f"Ensure {repo} has at least one release tagged "
            f"'{base_version}-nanvix-<version>'.",
        )

    tag = best.get("tag_name")
    if not isinstance(tag, str):
        log.fatal(
            f"Fallback release for {repo} has no tag_name",
            code=EXIT_NETWORK_ERROR,
        )

    from nanvix_zutil.buildroot import extract_nanvix_version

    nanvix_ver = extract_nanvix_version(tag)
    if nanvix_ver is None:
        log.fatal(
            f"Fallback release tag '{tag}' for {repo} does not contain "
            f"a nanvix version suffix",
            code=EXIT_NETWORK_ERROR,
        )

    return best, nanvix_ver


@overload
def download_release_asset(
    repo: str,
    version_specifier: str | int,
    asset_name: str,
    dest: Path,
    gh_token: str | None = None,
    *,
    match_prefix: bool = False,
    semver: bool = False,
    _release: dict[str, object] | None = None,
) -> Path: ...


@overload
def download_release_asset(
    repo: str,
    version_specifier: str | int,
    asset_name: str,
    dest: Path,
    gh_token: str | None = None,
    *,
    match_prefix: bool = False,
    semver: bool = False,
    _release: dict[str, object] | None = None,
    allow_missing: Literal[True],
) -> Path | None: ...


def download_release_asset(
    repo: str,
    version_specifier: str | int,
    asset_name: str,
    dest: Path,
    gh_token: str | None = None,
    *,
    match_prefix: bool = False,
    semver: bool = False,
    _release: dict[str, object] | None = None,
    allow_missing: bool = False,
) -> Path | None:
    """Download a GitHub release asset to *dest*.

    The file is saved as ``dest / asset_name`` (or ``dest / resolved_name``
    when *match_prefix* is ``True``).  If the file already exists it is
    returned immediately without re-downloading.

    Args:
        repo: Repository in ``owner/name`` format (e.g. ``"nanvix/zlib"``).
        version_specifier: Release tag, semver string, commit hash, ``"latest"``, or
            integer release ID.
        asset_name: The file name of the release asset.  When
            *match_prefix* is ``True`` this is treated as a name prefix
            and a matching asset whose name starts with *asset_name* is
            selected.
        dest: Directory where the asset will be written.
        gh_token: Optional GitHub personal access token.
        match_prefix: When ``True``, match assets whose name starts with
            *asset_name* instead of requiring an exact match.  Useful when
            the full asset name contains an unpredictable component such as
            a commit SHA.
        semver: When ``True``, enable the semver cascade for
            ``MAJOR.MINOR.PATCH`` strings.
        _release: Pre-resolved release metadata dictionary.  When
            provided, the internal release resolution step is skipped.
        allow_missing: When ``True``, return ``None`` instead of calling
            :func:`log.fatal` when the release or asset is not found.

    Returns:
        Path to the downloaded file, or ``None`` when *allow_missing* is
        ``True`` and the release or asset could not be found.

    Raises:
        SystemExit: With exit code ``4`` on network failure after all retries.
    """
    dest.mkdir(parents=True, exist_ok=True)

    # Fast path: check cache.
    if match_prefix:
        # Collect all matching cached files and select deterministically to
        # avoid relying on filesystem iteration order.
        matches = [
            cached
            for cached in dest.iterdir()
            if cached.is_file() and cached.name.startswith(asset_name)
        ]
        if matches:
            # Prefer the most recently modified file; break ties by name for
            # deterministic behavior across platforms.
            matches.sort(
                key=lambda p: (p.stat().st_mtime, p.name),  # type: ignore[attr-defined]
                reverse=True,
            )
            cached = matches[0]
            log.info(f"Asset already present: {cached}")
            return cached
    else:
        out_path = dest / asset_name
        if out_path.exists():
            log.info(f"Asset already present: {out_path}")
            return out_path

    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    release = _release or _resolve_release(
        repo, version_specifier, headers, semver=semver, allow_missing=allow_missing
    )
    if release is None:
        return None

    asset_url: str | None = None
    resolved_name: str = asset_name

    raw_assets: object = release.get("assets", [])
    if not isinstance(raw_assets, list):
        log.fatal(
            f"Unexpected assets format from GitHub API for {repo}@{version_specifier}",
            code=EXIT_NETWORK_ERROR,
        )
    assets = cast(list[object], raw_assets)
    for item in assets:
        if not isinstance(item, dict):
            continue
        asset = cast(dict[str, object], item)
        name = asset.get("name")
        if not isinstance(name, str):
            continue
        if match_prefix:
            if name.startswith(asset_name):
                resolved_name = name
                raw_url = asset.get("browser_download_url")
                if not isinstance(raw_url, str) or not raw_url:
                    log.fatal(
                        f"Malformed asset entry for {repo}@{version_specifier}: missing or invalid browser_download_url",
                        code=EXIT_NETWORK_ERROR,
                        hint="GitHub returned an asset without a usable browser_download_url; this may indicate an API change or a corrupted release.",
                    )
                asset_url = raw_url
                break
        else:
            if name == asset_name:
                raw_url = asset.get("browser_download_url")
                if not isinstance(raw_url, str) or not raw_url:
                    log.fatal(
                        f"Malformed asset entry for {repo}@{version_specifier}: missing or invalid browser_download_url",
                        code=EXIT_NETWORK_ERROR,
                        hint="GitHub returned an asset without a usable browser_download_url; this may indicate an API change or a corrupted release.",
                    )
                resolved_name = name
                asset_url = raw_url
                break

    if asset_url is None:
        if allow_missing:
            return None
        log.fatal(
            f"Asset '{asset_name}' not found in release {repo}@{version_specifier}",
            code=EXIT_MISSING_DEP,
            hint="Check the release tag and asset name.",
        )

    out_path = dest / resolved_name
    log.info(f"Downloading {resolved_name} from {repo}@{version_specifier}…")
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
            log.success(f"Downloaded {resolved_name}")
            return out_path
        except urllib.error.URLError as exc:
            if attempt == _MAX_RETRIES:
                log.fatal(
                    f"Failed to download {resolved_name}: {exc}",
                    code=EXIT_NETWORK_ERROR,
                    hint="Check your network connection or set GH_TOKEN.",
                )
            wait = _BACKOFF_BASE**attempt
            log.warning(f"Download attempt {attempt} failed; retrying in {wait:.0f}s…")
            time.sleep(wait)

    # Unreachable — log.fatal exits. Satisfy type checker.
    return out_path  # pragma: no cover
