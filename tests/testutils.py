# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Shared test helpers for nanvix_zutil tests."""

from pathlib import Path

_BUILDS_SECTION = (
    "\n"
    "[builds]\n"
    "[builds.matrix]\n"
    'platforms = ["hyperlight"]\n'
    'modes = ["multi-process"]\n'
    'memory = ["128mb"]\n'
)

# Minimal valid manifest content.
MINIMAL_MANIFEST = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "0.1.0"\n' + _BUILDS_SECTION
)

# Manifest with one build-time dependency.
MANIFEST_WITH_DEPS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "0.1.0"\n'
    "\n"
    "[dependencies]\n"
    'zlib = "1.0"\n' + _BUILDS_SECTION
)

# Manifest with "latest" sysroot and a VERSION dep.
MANIFEST_LATEST_WITH_DEPS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "latest"\n'
    "\n"
    "[dependencies]\n"
    'zlib = "1.3.1"\n' + _BUILDS_SECTION
)

# Manifest with a full [builds] section (platforms, modes, memory, excludes).
MANIFEST_WITH_BUILDS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "0.1.0"\n'
    "\n"
    "[builds]\n"
    "[builds.matrix]\n"
    'platforms = ["hyperlight", "microvm"]\n'
    'modes = ["multi-process", "standalone"]\n'
    'memory = ["128mb"]\n'
    "\n"
    "[[builds.exclude]]\n"
    'platform = "hyperlight"\n'
    'mode = "standalone"\n'
)


def write_manifest(repo_root: Path, content: str = MINIMAL_MANIFEST) -> None:
    """Create ``.nanvix/nanvix.toml`` inside *repo_root*."""
    nanvix_dir = repo_root / ".nanvix"
    nanvix_dir.mkdir(parents=True, exist_ok=True)
    (nanvix_dir / "nanvix.toml").write_text(content)
