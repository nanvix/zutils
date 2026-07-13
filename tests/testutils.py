# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Shared test helpers for nanvix_zutil tests."""

from nanvix_zutil.paths import manifest_path
from nanvix_zutil.sdk import SdkImage, SdkProvenance

SDK_DIGEST = f"sha256:{'a' * 64}"


def toolchain_toml(runtime: str = "0.1.0") -> str:
    """Return a canonical immutable SDK toolchain table."""
    sdk_runtime = runtime if runtime not in {"", "latest"} else "0.1.0"
    return (
        "\n[toolchain]\n"
        'kind = "nanvix-sdk"\n'
        'provider = "c-clang"\n'
        f'sdk-version = "v{sdk_runtime}-sdk.1"\n'
        'sdk-image = "ghcr.io/nanvix/nanvix-sdk-c-clang"\n'
        f'sdk-digest = "{SDK_DIGEST}"\n'
    )


def make_sdk_provenance(runtime: str = "0.1.0") -> SdkProvenance:
    """Return complete SDK provenance for lockfile tests."""
    image_name = "ghcr.io/nanvix/nanvix-sdk-c-clang"
    return SdkProvenance(
        sdk_version=f"v{runtime}-sdk.1",
        provider_id="c-clang",
        provider="clang",
        role="c",
        image=SdkImage(
            name=image_name,
            digest=SDK_DIGEST,
            ref=f"{image_name}@{SDK_DIGEST}",
        ),
        nanvix_tag=f"v{runtime}",
        nanvix_version=runtime,
        nanvix_commit="b" * 40,
        sysroot_sha256="c" * 64,
        compat={
            "c_abi": "i686-nanvix-sysv-1",
            "cxx_abi": "libc++",
            "abi": "static-elf",
            "min_nanvix_os": runtime,
        },
        target={"triple": "i686-unknown-nanvix", "alias": "i686-nanvix"},
        toolchain={
            "llvm_version": "22.1.8",
            "llvm_commit": "d" * 40,
            "port_branch": "nanvix/v22.1.8",
        },
        features={
            "localization": True,
            "filesystem": True,
            "wide_chars": True,
            "compiler_rt": "builtins-only",
            "dynamic_loader": False,
        },
    )


# Minimal valid manifest content.
MINIMAL_MANIFEST = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "0.1.0"\n' + toolchain_toml()
)

# Manifest with one build-time dependency.
MANIFEST_WITH_DEPS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "0.1.0"\n' + toolchain_toml() + "\n"
    "[dependencies]\n"
    'zlib = "1.0"\n'
)

MANIFEST_LATEST_WITH_DEPS = (
    "[package]\n"
    'name = "test"\n'
    'version = "0.1.0"\n'
    'nanvix-version = "latest"\n' + toolchain_toml() + "\n[dependencies]\n"
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
    For inline tables: ``deps={"zlib": '{ version = "1.0.0" }'}``.
    """
    lines = [
        "[package]",
        f'name = "{name}"',
        f'version = "{version}"',
        f'nanvix-version = "{nanvix_version}"',
    ]
    lines.extend(toolchain_toml(nanvix_version).strip().splitlines())
    if deps is not None:
        lines.append("[dependencies]")
        for dep_name, dep_value in deps.items():
            lines.append(f"{dep_name} = {dep_value}")
    if sys_deps is not None:
        lines.append("[system-dependencies]")
        for dep_name, dep_value in sys_deps.items():
            lines.append(f"{dep_name} = {dep_value}")
    return "\n".join(lines) + "\n"


def write_manifest(content: str = MINIMAL_MANIFEST) -> None:
    """Create ``.nanvix/nanvix.toml`` inside *repo_root*."""
    manifest_path().write_text(content)
