# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""TOML-based manifest parser for ``nanvix.toml``.

Parses a structured TOML manifest that declares package metadata and
dependencies.  ``nanvix-version`` must be a semver string (``X.Y.Z``)
or the literal ``"latest"``.  Dependency version fields accept a plain
string (``version`` specifier), or a table with one of ``version``,
``tag``, ``commitish``, or ``id``.

Environment variables ``NANVIX_VERSION`` (for the sysroot) and
``NANVIX_VERSION_<NAME>`` (for individual dependencies) override the
versions declared in the manifest.

Only ``version`` specifier refs (plain string or ``{ version = "..." }``)
are auto-suffixed with ``-nanvix-{sysroot_version}``.  ``tag``,
``commitish``, and ``id`` specifiers are exact-match and never suffixed.
Refs that already contain ``-nanvix-`` are rejected to prevent accidental
duplication.  When the sysroot is ``"latest"``, auto-suffixing is
deferred to the resolver (which resolves the actual version first).
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from typing import cast

from nanvix_zutil import log
from nanvix_zutil.buildroot import Dependency, Ref, RefKind
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS, EXIT_MISSING_DEP
from nanvix_zutil.paths import manifest_path
from nanvix_zutil.utils import SEMVER_RE

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Manifest:
    """Parsed contents of a ``nanvix.toml`` manifest file.

    Attributes:
        name: Package name.
        version: Package version.
        sysroot_ref: Nanvix sysroot version reference.
        dependencies: Build-time dependencies as :class:`Dependency` objects.
        system_dependencies: Runtime dependencies as :class:`Dependency` objects.
    """

    name: str
    version: str
    sysroot_ref: Ref
    dependencies: list[Dependency] = field(default_factory=lambda: [])
    system_dependencies: list[Dependency] = field(default_factory=lambda: [])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# We are intentionally keeping the semver matching simple for now.
_SPECIFIER_KEYS = frozenset({"version", "tag", "commitish", "id"})
_URL_UNSAFE = set("/\\#?%")

# Matches absolute Unix paths, Windows drive paths, and relative ./ or ../ paths.
_LOCAL_PATH_RE = re.compile(
    r"^(?:/|\./|\.\./|[A-Za-z]:[/\\])",
)


def is_local_path(value: str) -> bool:
    """Return ``True`` if *value* looks like a filesystem path.

    Recognised patterns:

    - Absolute Unix paths: ``/home/me/build``
    - Windows drive paths: ``C:\\Users\\me\\build`` or ``C:/build``
    - Relative paths: ``./build`` or ``../build``

    Args:
        value: The raw environment variable value.

    Returns:
        ``True`` if *value* matches a filesystem path pattern.
    """
    return _LOCAL_PATH_RE.match(value) is not None


def _validate_version_string(raw: str, context: str) -> str:
    """Validate a plain version string.

    The string must be non-empty and contain no whitespace.  The value
    ``"latest"`` is accepted but emits a warning.

    Args:
        raw: Raw version string.
        context: Human-readable label for error messages.

    Returns:
        The validated version string (unchanged).
    """
    if not raw or any(ch.isspace() for ch in raw):
        log.fatal(
            f"{manifest_path()}: invalid version for '{context}': '{raw}'",
            code=EXIT_INVALID_ARGS,
            hint=f"Version for {context} must be a non-empty string"
            " without whitespace.",
        )

    if any(ch in _URL_UNSAFE for ch in raw):
        log.fatal(
            f"{manifest_path()}: version for '{context}' contains URL-unsafe characters: '{raw}'",
            code=EXIT_INVALID_ARGS,
            hint="Version strings must not contain '/', '\\\\', '#', '?', or '%'.",
        )

    if raw == "latest":
        log.warning(
            f"{manifest_path()}: '{context}' is set to 'latest' — this is"
            " unlikely to match an actual release"
        )

    return raw


def _parse_nanvix_version(raw: object) -> Ref:
    """Parse ``nanvix-version``: semver ``X.Y.Z`` or ``"latest"``.

    Args:
        raw: The raw TOML value for ``nanvix-version``.

    Returns:
        A :class:`Ref` with kind :attr:`RefKind.TAG`.
    """
    if not isinstance(raw, str):
        log.fatal(
            f"{manifest_path()}: 'nanvix-version' must be a plain string"
            f" (got {type(raw).__name__})",
            code=EXIT_INVALID_ARGS,
            hint="Use 'nanvix-version = \"X.Y.Z\"' (semver) or"
            " 'nanvix-version = \"latest\"'.",
        )
    if raw == "latest":
        # "latest" is a supported first-class sysroot specifier; no warning needed.
        return Ref(kind=RefKind.TAG, value="latest")
    if not SEMVER_RE.match(raw):
        log.fatal(
            f"{manifest_path()}: 'nanvix-version' must be semver X.Y.Z or 'latest' (got '{raw}')",
            code=EXIT_INVALID_ARGS,
            hint="Use a version like 'nanvix-version = \"0.12.257\"'"
            " or 'nanvix-version = \"latest\"'.",
        )
    return Ref(kind=RefKind.TAG, value=raw)


def _parse_version_field(raw: object, context: str) -> Ref:
    """Parse a dependency version field.

    Accepts:

    - String → ``Ref(VERSION, value)`` — triggers auto-suffix
    - ``{ version = "..." }`` → ``Ref(VERSION, value)`` — triggers suffix
    - ``{ tag = "..." }`` → ``Ref(TAG, value)`` — exact match
    - ``{ commitish = "..." }`` → ``Ref(COMMITISH, value)`` — exact match
    - ``{ id = N }`` → ``Ref(ID, N)`` — direct release fetch

    Returns:
        A :class:`Ref` with the appropriate :class:`RefKind`.
    """
    if isinstance(raw, str):
        value = _validate_version_string(raw, context)
        return Ref(kind=RefKind.VERSION, value=value)

    if isinstance(raw, dict):
        raw_dict = cast("dict[str, object]", raw)

        found = _SPECIFIER_KEYS & raw_dict.keys()
        if len(found) > 1:
            log.fatal(
                f"{manifest_path()}: table for '{context}' has conflicting keys:"
                f" {', '.join(sorted(found))}",
                code=EXIT_INVALID_ARGS,
                hint="Use exactly one of 'version', 'tag', 'commitish', or 'id'.",
            )

        if "version" in raw_dict:
            ver_val: object = raw_dict["version"]
            if not isinstance(ver_val, str):
                log.fatal(
                    f"{manifest_path()}: 'version' value for '{context}' must be a string",
                    code=EXIT_INVALID_ARGS,
                )
            value = _validate_version_string(ver_val, f"{context}.version")
            return Ref(kind=RefKind.VERSION, value=value)

        if "tag" in raw_dict:
            tag_val: object = raw_dict["tag"]
            if not isinstance(tag_val, str):
                log.fatal(
                    f"{manifest_path()}: 'tag' value for '{context}' must be a string",
                    code=EXIT_INVALID_ARGS,
                )
            value = _validate_version_string(tag_val, f"{context}.tag")
            return Ref(kind=RefKind.TAG, value=value)

        if "commitish" in raw_dict:
            com_val: object = raw_dict["commitish"]
            if not isinstance(com_val, str):
                log.fatal(
                    f"{manifest_path()}: 'commitish' value for '{context}' must be a string",
                    code=EXIT_INVALID_ARGS,
                )
            value = _validate_version_string(com_val, f"{context}.commitish")
            return Ref(kind=RefKind.COMMITISH, value=value)

        if "id" in raw_dict:
            id_val: object = raw_dict["id"]
            if not isinstance(id_val, int):
                log.fatal(
                    f"{manifest_path()}: 'id' value for '{context}' must be an integer",
                    code=EXIT_INVALID_ARGS,
                )
            return Ref(kind=RefKind.ID, value=id_val)

        log.fatal(
            f"{manifest_path()}: table for '{context}' must contain one of"
            " 'version', 'tag', 'commitish', or 'id'",
            code=EXIT_INVALID_ARGS,
            hint=f"Use '{context} = {{ version = \"1.2.3\" }}',"
            f" '{context} = {{ tag = \"...\" }}',"
            f" '{context} = {{ commitish = \"...\" }}',"
            f" or '{context} = {{ id = 12345 }}'.",
        )

    log.fatal(
        f"{manifest_path()}: value for '{context}' must be a string or a table",
        code=EXIT_INVALID_ARGS,
        hint=f"Use '{context} = \"1.2.3\"' or '{context} = {{ version = \"1.2.3\" }}'.",
    )


def _parse_dependencies(
    section: dict[str, object],
    section_name: str,
) -> list[Dependency]:
    """Parse a ``[dependencies]`` or ``[system-dependencies]`` table.

    Each key becomes a :class:`Dependency` with ``repo=nanvix/<name>``
    and a :class:`Ref` set depending on the value format.
    """
    deps: list[Dependency] = []
    for name, raw_value in section.items():
        ref = _parse_version_field(raw_value, f"{section_name}.{name}")

        # Manifest values must not include the nanvix suffix — it is
        # derived automatically from nanvix-version.
        if (
            ref.kind == RefKind.VERSION
            and isinstance(ref.value, str)
            and "-nanvix-" in ref.value
        ):
            log.fatal(
                f"{manifest_path()}: dependency '{name}' must not include the nanvix"
                f" version in its ref (got '{ref.value}')",
                code=EXIT_INVALID_ARGS,
                hint=f"Use '{name} = \"{ref.value.split('-nanvix-')[0]}\"'"
                " — the nanvix version is derived automatically from"
                " nanvix-version.",
            )

        env_key = f"NANVIX_VERSION_{name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            # Env overrides MAY include "-nanvix-" for full control.
            # suffix_dep() will skip values that already contain it.
            if is_local_path(env_val):
                ref = Ref(kind=RefKind.LOCAL, value=env_val)
            else:
                ref = Ref(kind=ref.kind, value=env_val)

        deps.append(Dependency(name=name, repo=f"nanvix/{name}", ref=ref))
    return deps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_manifest() -> Manifest:
    """Parse a ``nanvix.toml`` manifest file.

    Reads the TOML file, validates required keys, enforces semver for
    ``nanvix-version``, parses dependency version fields (plain strings,
    ``{ version }``, ``{ tag }``, ``{ commitish }``, ``{ id }``), and
    auto-suffixes ``version`` refs with ``-nanvix-{sysroot_version}``.

    Environment variables ``NANVIX_VERSION`` (sysroot) and
    ``NANVIX_VERSION_<NAME>`` (per dependency) override manifest values.

    Returns:
        A :class:`Manifest` instance.

    Raises:
        SystemExit: With exit code ``3`` if the file does not exist, or
            exit code ``2`` if the manifest is malformed.
    """
    if not manifest_path().is_file():
        log.fatal(
            f"Required file not found: {manifest_path()}",
            code=EXIT_MISSING_DEP,
            hint="Create a nanvix.toml in the .nanvix/ directory.",
        )

    raw_bytes = manifest_path().read_bytes()
    try:
        data: dict[str, object] = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        log.fatal(
            f"{manifest_path()}: invalid TOML syntax: {exc}",
            code=EXIT_INVALID_ARGS,
        )

    # --- [package] (required) ---
    package: object = data.get("package")
    if not isinstance(package, dict):
        log.fatal(
            f"{manifest_path()}: missing required [package] section",
            code=EXIT_INVALID_ARGS,
            hint="Add a [package] section with 'name', 'version',"
            " and 'nanvix-version'.",
        )

    pkg = cast("dict[str, object]", package)

    pkg_name: object = pkg.get("name")
    if not isinstance(pkg_name, str) or not pkg_name:
        log.fatal(
            f"{manifest_path()}: missing or invalid [package] key 'name'",
            code=EXIT_INVALID_ARGS,
            hint="Add 'name = \"...\"' under [package].",
        )

    pkg_version: object = pkg.get("version")
    if not isinstance(pkg_version, str) or not pkg_version:
        log.fatal(
            f"{manifest_path()}: missing or invalid [package] key 'version'",
            code=EXIT_INVALID_ARGS,
            hint="Add 'version = \"...\"' under [package].",
        )

    raw_nanvix_version: object = pkg.get("nanvix-version")
    if raw_nanvix_version is None:
        log.fatal(
            f"{manifest_path()}: missing or invalid [package] key 'nanvix-version'",
            code=EXIT_INVALID_ARGS,
            hint="Add 'nanvix-version = \"...\"' under [package].",
        )

    sysroot_ref = _parse_nanvix_version(raw_nanvix_version)
    env_sysroot = os.environ.get("NANVIX_VERSION")
    if env_sysroot is not None:
        # NOTE: NANVIX_VERSION intentionally bypasses semver validation.
        # This is a development escape hatch — CI may pass with a non-semver
        # sysroot version if this env var is set.
        if is_local_path(env_sysroot):
            sysroot_ref = Ref(kind=RefKind.LOCAL, value=env_sysroot)
        else:
            sysroot_ref = Ref(kind=sysroot_ref.kind, value=env_sysroot)

    # --- [dependencies] (optional) ---
    deps_raw: object = data.get("dependencies", {})
    if not isinstance(deps_raw, dict):
        log.fatal(
            f"{manifest_path()}: [dependencies] must be a TOML table",
            code=EXIT_INVALID_ARGS,
        )
    dependencies = _parse_dependencies(
        cast("dict[str, object]", deps_raw), "dependencies"
    )

    # --- [system-dependencies] (optional) ---
    sys_deps_raw: object = data.get("system-dependencies", {})
    if not isinstance(sys_deps_raw, dict):
        log.fatal(
            f"{manifest_path()}: [system-dependencies] must be a TOML table",
            code=EXIT_INVALID_ARGS,
        )
    system_dependencies = _parse_dependencies(
        cast("dict[str, object]", sys_deps_raw), "system-dependencies"
    )

    # Auto-suffix VERSION refs with the nanvix sysroot version.
    # When the sysroot is "latest", suffixing is deferred to the resolver
    # (which resolves the sysroot first and knows the actual version).
    # Strip the leading "v" from the sysroot version to match the release
    # tag format used by nanvix-zutil (e.g. "1.3.1-nanvix-0.12.291").
    if (
        sysroot_ref.value != "latest"
        and isinstance(sysroot_ref.value, str)
        and sysroot_ref.kind != RefKind.LOCAL
    ):
        version_suffix = sysroot_ref.value.removeprefix("v")
        for dep in [*dependencies, *system_dependencies]:
            if dep.ref.kind == RefKind.VERSION and isinstance(dep.ref.value, str):
                if "-nanvix-" in dep.ref.value:
                    continue
                dep.ref = Ref(
                    kind=dep.ref.kind,
                    value=f"{dep.ref.value}-nanvix-{version_suffix}",
                )

    return Manifest(
        name=pkg_name,
        version=pkg_version,
        sysroot_ref=sysroot_ref,
        dependencies=dependencies,
        system_dependencies=system_dependencies,
    )
