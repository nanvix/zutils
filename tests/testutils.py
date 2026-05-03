# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Shared test helpers for nanvix_zutil tests."""

from pathlib import Path

# Minimal valid manifest content.
MINIMAL_MANIFEST = (
    "[package]\n" 'name = "test"\n' 'version = "0.1.0"\n' 'nanvix-version = "0.1.0"\n'
)

# Manifest with one build-time dependency.
MANIFEST_WITH_DEPS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "0.1.0"\n'
    "\n"
    "[dependencies]\n"
    'zlib = "1.0"\n'
)

# Manifest with "latest" sysroot and a VERSION dep.
MANIFEST_LATEST_WITH_DEPS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "latest"\n'
    "\n"
    "[dependencies]\n"
    'zlib = "1.3.1"\n'
)


def make_toml(
    *,
    name: str = "myapp",
    version: str = "1.0.0",
    nanvix_version: str = "0.12.257",
    deps: dict[str, str] | None = None,
    sys_deps: dict[str, str] | None = None,
) -> str:
    """Build a nanvix.toml string with sensible defaults.

    Dependency values are raw TOML fragments placed after ``=``.
    For string values, include quotes: ``deps={"zlib": '"1.0.0"'}``.
    For inline tables: ``deps={"zlib": '{ commitish = "abc" }'}``.
    """
    lines = [
        "[package]",
        f'name = "{name}"',
        f'version = "{version}"',
        f'nanvix-version = "{nanvix_version}"',
    ]
    if deps is not None:
        lines.append("[dependencies]")
        for dep_name, dep_value in deps.items():
            lines.append(f"{dep_name} = {dep_value}")
    if sys_deps is not None:
        lines.append("[system-dependencies]")
        for dep_name, dep_value in sys_deps.items():
            lines.append(f"{dep_name} = {dep_value}")
    return "\n".join(lines) + "\n"


def write_manifest(repo_root: Path, content: str = MINIMAL_MANIFEST) -> None:
    """Create ``.nanvix/nanvix.toml`` inside *repo_root*."""
    nanvix_dir = repo_root / ".nanvix"
    nanvix_dir.mkdir(parents=True, exist_ok=True)
    (nanvix_dir / "nanvix.toml").write_text(content)
