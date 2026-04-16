"""test_config.py — Tests for downstream_tests.config."""

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from downstream_tests.config import ensure_config, load_config


# ---------------------------------------------------------------------------
# ensure_config
# ---------------------------------------------------------------------------


def test_ensure_config_exists_already(tmp_path):
    """If config already exists, ensure_config returns it immediately."""
    cfg = tmp_path / "downstream.json"
    cfg.write_text(json.dumps({"defaults": {}, "consumers": []}))
    cache = tmp_path / "consumer-repos.json"

    result = ensure_config(cfg, cache)

    assert result == cfg


def test_ensure_config_fetches_remote(tmp_path):
    """When config is absent, ensure_config fetches the remote list and writes config."""
    cfg = tmp_path / "downstream.json"
    cache = tmp_path / "consumer-repos.json"
    repos = ["nanvix/zlib", "nanvix/sqlite"]
    raw = json.dumps(repos).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = ensure_config(cfg, cache)

    assert result == cfg
    data = json.loads(cfg.read_text())
    assert len(data["consumers"]) == 2
    assert data["consumers"][0]["repo"] == "nanvix/zlib"
    # Cache should also be written
    assert cache.exists()


def test_ensure_config_uses_cache_on_remote_failure(tmp_path):
    """When urlopen raises, ensure_config falls back to the local cache."""
    cfg = tmp_path / "downstream.json"
    cache = tmp_path / "consumer-repos.json"
    repos = ["nanvix/zlib"]
    cache.write_text(json.dumps(repos))

    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        result = ensure_config(cfg, cache)

    assert result == cfg
    data = json.loads(cfg.read_text())
    assert data["consumers"][0]["repo"] == "nanvix/zlib"


def test_ensure_config_fails_no_cache_no_remote(tmp_path):
    """When both network and cache are unavailable, ensure_config raises RuntimeError."""
    cfg = tmp_path / "downstream.json"
    cache = tmp_path / "consumer-repos.json"  # does not exist

    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        with pytest.raises(RuntimeError, match="Cannot fetch"):
            ensure_config(cfg, cache)


def test_ensure_config_dry_run(tmp_path):
    """In dry-run mode, ensure_config writes to a temp file (not config_path)."""
    cfg = tmp_path / "downstream.json"
    cache = tmp_path / "consumer-repos.json"
    repos = ["nanvix/zlib"]
    raw = json.dumps(repos).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = ensure_config(cfg, cache, dry_run=True)

    # Config path should NOT have been written
    assert not cfg.exists()
    # Cache should NOT be written in dry-run
    assert not cache.exists()
    # Returned path is a temp file that exists and is valid JSON
    assert result.exists()
    data = json.loads(result.read_text())
    assert "consumers" in data


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_expands_tilde(tmp_path):
    """load_config expands ~ in repos_root."""
    cfg = tmp_path / "downstream.json"
    cfg.write_text(json.dumps({"defaults": {"repos_root": "~/repos"}, "consumers": []}))
    data = load_config(cfg)
    assert "~" not in data["defaults"]["repos_root"]


def test_load_config_defaults(tmp_path):
    """Missing keys in defaults get sensible values."""
    cfg = tmp_path / "downstream.json"
    cfg.write_text(json.dumps({}))
    data = load_config(cfg)
    defaults = data["defaults"]
    assert defaults["checkout_strategy"] == "shallow"
    assert defaults["win_repos_root"] is None
    assert defaults["branch_pattern"] == "nanvix/v*"
    assert data["consumers"] == []


def test_load_config_per_consumer_overrides(tmp_path):
    """Per-consumer strategy and branch are preserved."""
    cfg = tmp_path / "downstream.json"
    consumers = [
        {"repo": "nanvix/zlib", "strategy": "clone", "branch": "nanvix/v1.0.0"}
    ]
    cfg.write_text(json.dumps({"consumers": consumers}))
    data = load_config(cfg)
    c = data["consumers"][0]
    assert c["strategy"] == "clone"
    assert c["branch"] == "nanvix/v1.0.0"
