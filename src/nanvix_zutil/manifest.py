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
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from nanvix_zutil import log
from nanvix_zutil.utils import SEMVER_RE
from nanvix_zutil.buildroot import Dependency, Ref, RefKind
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS, EXIT_MISSING_DEP

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildMatrix:
    """Parsed ``[builds]`` section from ``nanvix.toml``.

    Attributes:
        dimensions: Mapping of dimension names to allowed values.
            Keys are ``"platforms"``, ``"modes"``, ``"memory"``.
        exclude: List of partial-match dicts. Each dict maps
            combo field names (singular: ``"platform"``, ``"mode"``,
            ``"memory"``) to values; matching combos are removed.
    """

    dimensions: dict[str, list[str]]
    exclude: list[dict[str, str]]


@dataclass
class Manifest:
    """Parsed contents of a ``nanvix.toml`` manifest file.

    Attributes:
        name: Package name.
        version: Package version.
        sysroot_ref: Nanvix sysroot version reference.
        builds: Build matrix from the required ``[builds]`` section.
        dependencies: Build-time dependencies as :class:`Dependency` objects.
        system_dependencies: Runtime dependencies as :class:`Dependency` objects.
    """

    name: str
    version: str
    sysroot_ref: Ref
    builds: BuildMatrix
    dependencies: list[Dependency] = field(default_factory=list)
    system_dependencies: list[Dependency] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# We are intentionally keeping the semver matching simple for now.
_SPECIFIER_KEYS = frozenset({"version", "tag", "commitish", "id"})
_URL_UNSAFE = set("/\\#?%")


def _is_local_path(value: str) -> bool:  # pyright: ignore[reportUnusedFunction]
    """Return ``True`` if *value* looks like a filesystem path.

    Detects Unix absolute paths (``/``), home-directory paths (``~/``),
    relative paths (``./``, ``../``), Windows drive-letter paths
    (``C:\\``, ``D:/``), and Windows UNC paths (``\\\\server\\share``).
    """
    if value.startswith(("/", "./", "../", "~/")):
        return True
    # Windows drive letter: C:\, D:/, etc.
    if len(value) >= 3 and value[1] == ":" and value[2] in ("/", "\\"):
        return True
    # Windows UNC path: \\server\share
    if value.startswith("\\\\"):
        return True
    return False


def _validate_version_string(raw: str, context: str, path: Path) -> str:
    """Validate a plain version string.

    The string must be non-empty and contain no whitespace.  The value
    ``"latest"`` is accepted but emits a warning.

    Args:
        raw: Raw version string.
        context: Human-readable label for error messages.
        path: Manifest file path (for error messages).

    Returns:
        The validated version string (unchanged).
    """
    if not raw or any(ch.isspace() for ch in raw):
        log.fatal(
            f"{path}: invalid version for '{context}': '{raw}'",
            code=EXIT_INVALID_ARGS,
            hint=f"Version for {context} must be a non-empty string"
            " without whitespace.",
        )

    if any(ch in _URL_UNSAFE for ch in raw):
        log.fatal(
            f"{path}: version for '{context}' contains URL-unsafe characters: '{raw}'",
            code=EXIT_INVALID_ARGS,
            hint="Version strings must not contain '/', '\\\\', '#', '?', or '%'.",
        )

    if raw == "latest":
        log.warning(
            f"{path}: '{context}' is set to 'latest' — this is"
            " unlikely to match an actual release"
        )

    return raw


def _parse_nanvix_version(raw: object, path: Path) -> Ref:
    """Parse ``nanvix-version``: semver ``X.Y.Z`` or ``"latest"``.

    Args:
        raw: The raw TOML value for ``nanvix-version``.
        path: Manifest file path (for error messages).

    Returns:
        A :class:`Ref` with kind :attr:`RefKind.TAG`.
    """
    if not isinstance(raw, str):
        log.fatal(
            f"{path}: 'nanvix-version' must be a plain string"
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
            f"{path}: 'nanvix-version' must be semver X.Y.Z or 'latest' (got '{raw}')",
            code=EXIT_INVALID_ARGS,
            hint="Use a version like 'nanvix-version = \"0.12.257\"'"
            " or 'nanvix-version = \"latest\"'.",
        )
    return Ref(kind=RefKind.TAG, value=raw)


def _parse_version_field(raw: object, context: str, path: Path) -> Ref:
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
        value = _validate_version_string(raw, context, path)
        return Ref(kind=RefKind.VERSION, value=value)

    if isinstance(raw, dict):
        raw_dict = cast("dict[str, object]", raw)

        found = _SPECIFIER_KEYS & raw_dict.keys()
        if len(found) > 1:
            log.fatal(
                f"{path}: table for '{context}' has conflicting keys:"
                f" {', '.join(sorted(found))}",
                code=EXIT_INVALID_ARGS,
                hint="Use exactly one of 'version', 'tag', 'commitish', or 'id'.",
            )

        if "version" in raw_dict:
            ver_val: object = raw_dict["version"]
            if not isinstance(ver_val, str):
                log.fatal(
                    f"{path}: 'version' value for '{context}' must be a string",
                    code=EXIT_INVALID_ARGS,
                )
            value = _validate_version_string(ver_val, f"{context}.version", path)
            return Ref(kind=RefKind.VERSION, value=value)

        if "tag" in raw_dict:
            tag_val: object = raw_dict["tag"]
            if not isinstance(tag_val, str):
                log.fatal(
                    f"{path}: 'tag' value for '{context}' must be a string",
                    code=EXIT_INVALID_ARGS,
                )
            value = _validate_version_string(tag_val, f"{context}.tag", path)
            return Ref(kind=RefKind.TAG, value=value)

        if "commitish" in raw_dict:
            com_val: object = raw_dict["commitish"]
            if not isinstance(com_val, str):
                log.fatal(
                    f"{path}: 'commitish' value for '{context}' must be a string",
                    code=EXIT_INVALID_ARGS,
                )
            value = _validate_version_string(com_val, f"{context}.commitish", path)
            return Ref(kind=RefKind.COMMITISH, value=value)

        if "id" in raw_dict:
            id_val: object = raw_dict["id"]
            if not isinstance(id_val, int):
                log.fatal(
                    f"{path}: 'id' value for '{context}' must be an integer",
                    code=EXIT_INVALID_ARGS,
                )
            return Ref(kind=RefKind.ID, value=id_val)

        log.fatal(
            f"{path}: table for '{context}' must contain one of"
            " 'version', 'tag', 'commitish', or 'id'",
            code=EXIT_INVALID_ARGS,
            hint=f"Use '{context} = {{ version = \"1.2.3\" }}',"
            f" '{context} = {{ tag = \"...\" }}',"
            f" '{context} = {{ commitish = \"...\" }}',"
            f" or '{context} = {{ id = 12345 }}'.",
        )

    log.fatal(
        f"{path}: value for '{context}' must be a string or a table",
        code=EXIT_INVALID_ARGS,
        hint=f"Use '{context} = \"1.2.3\"' or '{context} = {{ version = \"1.2.3\" }}'.",
    )


def parse_builds_section(
    raw: dict[str, object],
    path: Path,
) -> BuildMatrix:
    """Parse a ``[builds]`` table into a :class:`BuildMatrix`.

    Validates that ``[builds.matrix]`` contains exactly the required
    dimensions (``platforms``, ``modes``, ``memory``), each as a
    non-empty list of strings.  Exclude entries use singular field
    names (``platform``, ``mode``, ``memory``) and are validated
    against the known set — unknown keys are rejected.

    Args:
        raw: The raw TOML table for the ``[builds]`` section.
        path: Manifest file path (for error messages).

    Returns:
        A :class:`BuildMatrix` with validated dimensions and excludes.

    Raises:
        SystemExit: With exit code ``2`` on any validation failure.
    """
    matrix_raw: object = raw.get("matrix")
    if not isinstance(matrix_raw, dict):
        log.fatal(
            f"{path}: [builds] missing required 'matrix' key",
            code=EXIT_INVALID_ARGS,
        )
    matrix = cast("dict[str, object]", matrix_raw)

    _valid_dimensions = frozenset({"platforms", "modes", "memory"})
    unknown_dims = set(matrix.keys()) - _valid_dimensions
    if unknown_dims:
        log.fatal(
            f"{path}: [builds.matrix] has unknown dimension(s):"
            f" {', '.join(sorted(unknown_dims))}"
            f" (valid: {', '.join(sorted(_valid_dimensions))})",
            code=EXIT_INVALID_ARGS,
        )

    _required_dimensions = ("platforms", "modes", "memory")
    missing_dims = [d for d in _required_dimensions if d not in matrix]
    if missing_dims:
        log.fatal(
            f"{path}: [builds.matrix] is missing required dimension(s):"
            f" {', '.join(missing_dims)}",
            code=EXIT_INVALID_ARGS,
        )

    dimensions: dict[str, list[str]] = {}
    for dim_name, dim_val in matrix.items():
        if not isinstance(dim_val, list):
            log.fatal(
                f"{path}: [builds.matrix.{dim_name}] must be a list of strings",
                code=EXIT_INVALID_ARGS,
            )
        str_list: list[str] = []
        for item in cast("list[object]", dim_val):
            if not isinstance(item, str):
                log.fatal(
                    f"{path}: [builds.matrix.{dim_name}] must be a list of"
                    " strings (non-string value found)",
                    code=EXIT_INVALID_ARGS,
                )
            str_list.append(item)
        if not str_list:
            log.fatal(
                f"{path}: [builds.matrix.{dim_name}] must have at least one value",
                code=EXIT_INVALID_ARGS,
            )
        dimensions[dim_name] = str_list

    exclude: list[dict[str, str]] = []
    exclude_raw: object = raw.get("exclude")
    if exclude_raw is not None:
        if not isinstance(exclude_raw, list):
            log.fatal(
                f"{path}: [builds] 'exclude' must be an array of tables",
                code=EXIT_INVALID_ARGS,
            )
        _valid_combo_fields = frozenset({"platform", "mode", "memory"})
        for exc_item in cast("list[object]", exclude_raw):
            if not isinstance(exc_item, dict):
                log.fatal(
                    f"{path}: each [[builds.exclude]] entry must be a table",
                    code=EXIT_INVALID_ARGS,
                )
            exc_dict = cast("dict[str, object]", exc_item)
            if not exc_dict:
                log.fatal(
                    f"{path}: [[builds.exclude]] entry must not be empty"
                    " — an empty table would exclude every combination",
                    code=EXIT_INVALID_ARGS,
                )
            str_exc: dict[str, str] = {}
            for k, v in exc_dict.items():
                if k not in _valid_combo_fields:
                    log.fatal(
                        f"{path}: [[builds.exclude]] references unknown"
                        f" field '{k}' (valid: {', '.join(sorted(_valid_combo_fields))})",
                        code=EXIT_INVALID_ARGS,
                    )
                if not isinstance(v, str):
                    log.fatal(
                        f"{path}: [[builds.exclude]] value for '{k}' must be a string",
                        code=EXIT_INVALID_ARGS,
                    )
                str_exc[k] = v
            exclude.append(str_exc)

    return BuildMatrix(dimensions=dimensions, exclude=exclude)


def _parse_dependencies(
    section: dict[str, object],
    path: Path,
    section_name: str,
) -> list[Dependency]:
    """Parse a ``[dependencies]`` or ``[system-dependencies]`` table.

    Each key becomes a :class:`Dependency` with ``repo=nanvix/<name>``
    and a :class:`Ref` set depending on the value format.
    """
    deps: list[Dependency] = []
    for name, raw_value in section.items():
        ref = _parse_version_field(raw_value, f"{section_name}.{name}", path)

        env_key = f"NANVIX_VERSION_{name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            ref = Ref(kind=ref.kind, value=env_val)

        if (
            ref.kind == RefKind.VERSION
            and isinstance(ref.value, str)
            and "-nanvix-" in ref.value
        ):
            log.fatal(
                f"{path}: dependency '{name}' must not include the nanvix"
                f" version in its ref (got '{ref.value}')",
                code=EXIT_INVALID_ARGS,
                hint=f"Use '{name} = \"{ref.value.split('-nanvix-')[0]}\"'"
                " — the nanvix version is derived automatically from"
                " nanvix-version.",
            )

        deps.append(Dependency(name=name, repo=f"nanvix/{name}", ref=ref))
    return deps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_manifest(path: Path) -> Manifest:
    """Parse a ``nanvix.toml`` manifest file.

    Reads the TOML file, validates required keys, enforces semver for
    ``nanvix-version``, parses dependency version fields (plain strings,
    ``{ version }``, ``{ tag }``, ``{ commitish }``, ``{ id }``), and
    auto-suffixes ``version`` refs with ``-nanvix-{sysroot_version}``.

    Environment variables ``NANVIX_VERSION`` (sysroot) and
    ``NANVIX_VERSION_<NAME>`` (per dependency) override manifest values.

    Args:
        path: Path to the ``nanvix.toml`` file.

    Returns:
        A :class:`Manifest` instance.

    Raises:
        SystemExit: With exit code ``3`` if the file does not exist, or
            exit code ``2`` if the manifest is malformed.
    """
    if not path.is_file():
        log.fatal(
            f"Required file not found: {path}",
            code=EXIT_MISSING_DEP,
            hint="Create a nanvix.toml in the .nanvix/ directory.",
        )

    raw_bytes = path.read_bytes()
    try:
        data: dict[str, object] = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        log.fatal(
            f"{path}: invalid TOML syntax: {exc}",
            code=EXIT_INVALID_ARGS,
        )

    # --- [package] (required) ---
    package: object = data.get("package")
    if not isinstance(package, dict):
        log.fatal(
            f"{path}: missing required [package] section",
            code=EXIT_INVALID_ARGS,
            hint="Add a [package] section with 'name', 'version',"
            " and 'nanvix-version'.",
        )

    pkg = cast("dict[str, object]", package)

    pkg_name: object = pkg.get("name")
    if not isinstance(pkg_name, str) or not pkg_name:
        log.fatal(
            f"{path}: missing or invalid [package] key 'name'",
            code=EXIT_INVALID_ARGS,
            hint="Add 'name = \"...\"' under [package].",
        )

    pkg_version: object = pkg.get("version")
    if not isinstance(pkg_version, str) or not pkg_version:
        log.fatal(
            f"{path}: missing or invalid [package] key 'version'",
            code=EXIT_INVALID_ARGS,
            hint="Add 'version = \"...\"' under [package].",
        )

    raw_nanvix_version: object = pkg.get("nanvix-version")
    if raw_nanvix_version is None:
        log.fatal(
            f"{path}: missing or invalid [package] key 'nanvix-version'",
            code=EXIT_INVALID_ARGS,
            hint="Add 'nanvix-version = \"...\"' under [package].",
        )

    sysroot_ref = _parse_nanvix_version(raw_nanvix_version, path)
    env_sysroot = os.environ.get("NANVIX_VERSION")
    if env_sysroot is not None:
        # NOTE: NANVIX_VERSION intentionally bypasses semver validation.
        # This is a development escape hatch — CI may pass with a non-semver
        # sysroot version if this env var is set.
        sysroot_ref = Ref(kind=sysroot_ref.kind, value=env_sysroot)

    # --- [dependencies] (optional) ---
    deps_raw: object = data.get("dependencies", {})
    if not isinstance(deps_raw, dict):
        log.fatal(
            f"{path}: [dependencies] must be a TOML table",
            code=EXIT_INVALID_ARGS,
        )
    dependencies = _parse_dependencies(
        cast("dict[str, object]", deps_raw), path, "dependencies"
    )

    # --- [system-dependencies] (optional) ---
    sys_deps_raw: object = data.get("system-dependencies", {})
    if not isinstance(sys_deps_raw, dict):
        log.fatal(
            f"{path}: [system-dependencies] must be a TOML table",
            code=EXIT_INVALID_ARGS,
        )
    system_dependencies = _parse_dependencies(
        cast("dict[str, object]", sys_deps_raw), path, "system-dependencies"
    )

    # --- [builds] (required) ---
    builds_raw: object = data.get("builds")
    if builds_raw is None:
        log.fatal(
            f"{path}: missing required [builds] section",
            code=EXIT_INVALID_ARGS,
        )
    if not isinstance(builds_raw, dict):
        log.fatal(
            f"{path}: [builds] must be a TOML table",
            code=EXIT_INVALID_ARGS,
        )
    builds = parse_builds_section(cast("dict[str, object]", builds_raw), path)

    # Auto-suffix VERSION refs with the nanvix sysroot version.
    # When the sysroot is "latest", suffixing is deferred to the resolver
    # (which resolves the sysroot first and knows the actual version).
    # Strip the leading "v" from the sysroot version to match the release
    # tag format used by nanvix-zutil (e.g. "1.3.1-nanvix-0.12.291").
    if sysroot_ref.value != "latest" and isinstance(sysroot_ref.value, str):
        version_suffix = sysroot_ref.value.removeprefix("v")
        for dep in [*dependencies, *system_dependencies]:
            if dep.ref.kind == RefKind.VERSION and isinstance(dep.ref.value, str):
                dep.ref = Ref(
                    kind=dep.ref.kind,
                    value=f"{dep.ref.value}-nanvix-{version_suffix}",
                )

    return Manifest(
        name=pkg_name,
        version=pkg_version,
        sysroot_ref=sysroot_ref,
        builds=builds,
        dependencies=dependencies,
        system_dependencies=system_dependencies,
    )
