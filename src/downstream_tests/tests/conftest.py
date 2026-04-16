"""conftest.py — Shared fixtures for downstream_tests test suite."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def sample_consumer_repos():
    return ["nanvix/zlib", "nanvix/sqlite"]


@pytest.fixture
def sample_config(tmp_path, sample_consumer_repos):
    config = {
        "$schema": "./downstream.schema.json",
        "defaults": {
            "checkout_strategy": "shallow",
            "repos_root": str(tmp_path / "repos"),
            "win_repos_root": None,
            "branch_pattern": "nanvix/v*",
        },
        "consumers": [{"repo": r} for r in sample_consumer_repos],
    }
    p = tmp_path / "downstream.json"
    p.write_text(json.dumps(config))
    return p


@pytest.fixture
def sample_cache(tmp_path, sample_consumer_repos):
    p = tmp_path / "consumer-repos.json"
    p.write_text(json.dumps(sample_consumer_repos))
    return p
