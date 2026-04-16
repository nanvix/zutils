"""fallback.py -- Fallback environment helpers for downstream_tests."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .log import log


def export_fallback_env(repo_dir: Path) -> None:
    """Parse ``.nanvix/nanvix.toml`` and set NANVIX_VERSION_* env vars.

    Reads the ``[dependencies]`` section and for each entry sets
    ``NANVIX_VERSION_<NAME>`` to ``<version>-nanvix-99.99.99``.  This forces
    ``resolve_release_with_fallback()`` to miss the exact tag and fall back to
    the best available release.

    .. note::

        This function mutates ``os.environ`` directly.  The env vars persist
        for the lifetime of the process, which is acceptable because each
        consumer is expected to run in its own subprocess (via the venv
        python).  If multiple consumers are ever run in-process, this should
        be refactored to return a dict and pass it via ``env=``.

    Args:
        repo_dir: Root of the consumer repo.

    Raises:
        RuntimeError: If ``.nanvix/nanvix.toml`` does not exist.
    """
    manifest = repo_dir / ".nanvix" / "nanvix.toml"
    if not manifest.exists():
        raise RuntimeError(f"  No nanvix.toml at {manifest}")

    in_deps = False
    dep_re = re.compile(r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]+)"')
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("[dependencies]"):
            in_deps = True
            continue
        if line.strip().startswith("["):
            in_deps = False
            continue
        if in_deps:
            m = dep_re.match(line)
            if m:
                raw_name = m.group(1)
                version = m.group(2)
                env_key = "NANVIX_VERSION_" + raw_name.upper().replace("-", "_")
                env_val = f"{version}-nanvix-99.99.99"
                os.environ[env_key] = env_val
                log(f"  {env_key}={env_val}")
