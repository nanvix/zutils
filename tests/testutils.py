# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Shared test helpers for nanvix_zutil tests."""

from pathlib import Path

# Minimal valid manifest content.
MINIMAL_MANIFEST = (
    "[package]\n" 'name = "test"\n' 'version = "0.1.0"\n' 'nanvix-version = "0.1.0"\n'
)


def write_manifest(repo_root: Path, content: str = MINIMAL_MANIFEST) -> None:
    """Create ``.nanvix/nanvix.toml`` inside *repo_root*."""
    nanvix_dir = repo_root / ".nanvix"
    nanvix_dir.mkdir(parents=True, exist_ok=True)
    (nanvix_dir / "nanvix.toml").write_text(content)
