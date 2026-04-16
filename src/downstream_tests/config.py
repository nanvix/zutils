# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""config.py -- Config loading helpers for downstream_tests."""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from .log import dry, log

CONSUMERS_URL = (
    "https://raw.githubusercontent.com/nanvix/workflows/refs/heads/main/"
    "consumer-repos.json"
)


def ensure_config(
    config_path: Path,
    cache_path: Path,
    *,
    dry_run: bool = False,
) -> Path:
    """Ensure downstream.json exists, generating it from consumer-repos.json.

    Fetch consumer-repos.json from the remote URL and cache it locally.
    Transform the list into a downstream.json structure.  In dry-run mode,
    write to a temporary file and return that path so the rest of the script
    can still read a valid config.

    Args:
        config_path: Desired path for downstream.json.
        cache_path:  Path for the cached consumer-repos.json.
        dry_run:     When True, write the generated config to a temp file
                     instead of *config_path*.

    Returns:
        The effective config path (may be a temp file in dry-run mode).

    Raises:
        RuntimeError: If the remote fetch fails and no cache is available.
    """
    if config_path.exists():
        return config_path

    log("No downstream.json found -- generating from consumer-repos.json...")

    repos: list[str] = []
    fetched = False

    try:
        with urllib.request.urlopen(CONSUMERS_URL, timeout=30) as resp:
            raw = resp.read().decode()
        repos = json.loads(raw)
        if not dry_run:
            cache_path.write_text(raw, encoding="utf-8")
        log("  Fetched consumer list from remote")
        fetched = True
    except Exception as exc:
        log(f"  Warning: failed to fetch consumer list: {exc}")

    if not fetched:
        if cache_path.exists():
            repos = json.loads(cache_path.read_text(encoding="utf-8"))
            log("  Using cached consumer-repos.json")
        else:
            raise RuntimeError(
                f"Cannot fetch consumer list and no cache at {cache_path}"
            )

    config_data = {
        "$schema": "./downstream.schema.json",
        "defaults": {
            "checkout_strategy": "shallow",
            "repos_root": "~/repos",
            "win_repos_root": None,
            "branch_pattern": "nanvix/v*",
        },
        "consumers": [{"repo": r} for r in repos],
    }
    config_json = json.dumps(config_data, indent=2)

    if dry_run:
        dry(f"would generate {config_path} from consumer-repos.json")
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        )
        tmp.write(config_json)
        tmp.close()
        return Path(tmp.name)

    config_path.write_text(config_json, encoding="utf-8")
    log(f"Generated {config_path} -- customize as needed.")
    return config_path


def load_config(config_path: Path) -> dict[str, Any]:
    """Read and parse downstream.json, applying defaults for missing keys.

    Expands ``~`` in ``repos_root``.  Sets sensible defaults for any key
    absent from the ``defaults`` section.

    Args:
        config_path: Path to downstream.json.

    Returns:
        Parsed config dict with defaults filled in.
    """
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    defaults = raw.setdefault("defaults", {})

    repos_root = defaults.get("repos_root", "~/repos")
    defaults["repos_root"] = str(Path(repos_root).expanduser())

    defaults.setdefault("checkout_strategy", "shallow")
    defaults.setdefault("win_repos_root", None)
    defaults.setdefault("branch_pattern", "nanvix/v*")

    raw.setdefault("consumers", [])
    return raw
