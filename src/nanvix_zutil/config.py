# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Persistent key-value configuration for nanvix_zutil consumers.

Configuration is stored at ``.nanvix/env.json`` and overridden by environment
variables at runtime.  The precedence order (highest to lowest) is:

1. Environment variables
2. Persisted ``.nanvix/env.json``
3. Built-in defaults
"""

from __future__ import annotations

import json
import os
import sys
from enum import StrEnum
from typing import cast, overload

from nanvix_zutil import log
from nanvix_zutil.paths import nanvix_root

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Host(StrEnum):
    """Development host operating system."""

    linux = "linux"
    windows = "windows"


class Target(StrEnum):
    """Target CPU architecture."""

    x86 = "x86"
    arm = "arm"


class Machine(StrEnum):
    """Target virtual machine."""

    microvm = "microvm"
    hyperlight = "hyperlight"


class DeploymentMode(StrEnum):
    """Deployment mode."""

    single_process = "single-process"
    multi_process = "multi-process"
    standalone = "standalone"


class MemorySize(StrEnum):
    """Memory size used for artifact naming."""

    mb128 = "128mb"
    mb256 = "256mb"


def _default_host() -> str:
    """Return the default host string for the current platform."""
    return Host.windows.value if sys.platform == "win32" else Host.linux.value


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, str] = {
    "NANVIX_HOST": _default_host(),
    "NANVIX_TARGET": Target.x86.value,
    "NANVIX_MACHINE": Machine.microvm.value,
    "NANVIX_DEPLOYMENT_MODE": DeploymentMode.standalone.value,
    "NANVIX_MEMORY_SIZE": MemorySize.mb256.value,
}

DEFAULT_HOST: str = _DEFAULTS["NANVIX_HOST"]
"""Default development host (platform-dependent)."""

DEFAULT_TARGET: str = _DEFAULTS["NANVIX_TARGET"]
"""Default target architecture."""

DEFAULT_MACHINE: str = _DEFAULTS["NANVIX_MACHINE"]
"""Default target machine identifier."""

DEFAULT_DEPLOYMENT_MODE: str = _DEFAULTS["NANVIX_DEPLOYMENT_MODE"]
"""Default deployment mode."""

DEFAULT_MEMORY_SIZE: str = _DEFAULTS["NANVIX_MEMORY_SIZE"]
"""Default memory size string for artifact naming."""

_ENUMS: dict[str, type[StrEnum]] = {
    "NANVIX_HOST": Host,
    "NANVIX_TARGET": Target,
    "NANVIX_MACHINE": Machine,
    "NANVIX_DEPLOYMENT_MODE": DeploymentMode,
    "NANVIX_MEMORY_SIZE": MemorySize,
}

# ---------------------------------------------------------------------------
# Standard config key names
# ---------------------------------------------------------------------------

CFG_SYSROOT: str = "NANVIX_SYSROOT"
"""Path to the downloaded Nanvix sysroot directory."""

CFG_GH_TOKEN: str = "GH_TOKEN"
"""GitHub token for authenticated API requests (rate limits)."""

CFG_DOCKER_IMAGE: str = "NANVIX_DOCKER_IMAGE"
"""Docker image persisted by ``setup --with-docker``."""

#: Curated mapping of the most common environment variables recognised by
#: nanvix-zutil to human-readable descriptions.  Rendered in the ``--help``
#: epilog.  Not exhaustive — consumers and other modules may honour additional
#: ``NANVIX_*`` variables recognised by manifest/script modules.
ENV_VARS: dict[str, str] = {
    "NANVIX_HOST": f"Development host (default: {DEFAULT_HOST}; one of: {', '.join(Host)})",
    "NANVIX_TARGET": f"Target architecture (default: {DEFAULT_TARGET}; one of: {', '.join(Target)})",
    "NANVIX_MACHINE": f"Target machine (default: {DEFAULT_MACHINE}; one of: {', '.join(Machine)})",
    "NANVIX_DEPLOYMENT_MODE": f"Deployment mode (default: {DEFAULT_DEPLOYMENT_MODE}; one of: {', '.join(DeploymentMode)})",
    "NANVIX_MEMORY_SIZE": f"Memory size for artifact naming (default: {DEFAULT_MEMORY_SIZE}; one of: {', '.join(MemorySize)})",
    "NANVIX_SYSROOT": "Path to runtime sysroot (set by setup)",
    "NANVIX_DOCKER_IMAGE": "Docker image override (set by setup --with-docker)",
    "GH_TOKEN": "GitHub token for API rate limits",
}


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class Config:
    """Persistent key-value configuration stored at ``.nanvix/env.json``.

    Initialisation order:

    1. Seed with built-in defaults.
    2. Override with values from the persisted ``.nanvix/env.json`` file,
       if it exists.
    3. Override with environment variables (including extra ``NANVIX_*`` keys
       and ``GH_TOKEN``), which always take precedence.

    Effective precedence (highest to lowest) is therefore:

    1. Environment variables
    2. Persisted ``.nanvix/env.json``
    3. Built-in defaults

    Attributes:
        host: Development host operating system as :class:`Host`
            (e.g. ``Host.linux``).
        target: Target CPU architecture as :class:`Target`
            (e.g. ``Target.x86``).
        machine: Target machine identifier as :class:`Machine`
            (e.g. ``Machine.microvm``).
        deployment_mode: Deployment mode as :class:`DeploymentMode`.
        memory_size: Memory size as :class:`MemorySize`.

    All enum values are :class:`str` subclasses (``StrEnum``), so bare-string
    equality and f-string interpolation keep working for existing callers.
    """

    def __init__(self) -> None:
        """Initialise configuration from environment and persisted state.

        The ``.nanvix/`` directory is resolved lazily via
        :func:`nanvix_zutil.paths.nanvix_root`.
        """
        self._config_path = nanvix_root() / "env.json"
        self._data: dict[str, str] = {}

        # Seed with defaults.
        self._data.update(_DEFAULTS)

        # Load persisted values (environment still wins below).
        if self._config_path.exists():
            self.load()
            # Never persist secrets such as GH_TOKEN; strip if present.
            self._data.pop("GH_TOKEN", None)

        # Apply environment variable overrides.
        for key in list(self._data.keys()):
            env_val = os.environ.get(key)
            if env_val is not None:
                self._data[key] = env_val

        # Apply any extra env vars not in defaults.
        for key, val in os.environ.items():
            if key.startswith("NANVIX_"):
                self._data[key] = val

        # Validate enum-typed keys; fatal on invalid.
        for key, enum_cls in _ENUMS.items():
            val = self._data[key]
            try:
                enum_cls(val)
            except ValueError:
                allowed = ", ".join(enum_cls)
                log.fatal(
                    f"Invalid value for {key}: {val!r}.",
                    hint=f"Allowed values: {allowed}.",
                )

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def host(self) -> Host:
        """Development host operating system."""
        return Host(self._data.get("NANVIX_HOST", _DEFAULTS["NANVIX_HOST"]))

    @property
    def target(self) -> Target:
        """Target CPU architecture."""
        return Target(self._data.get("NANVIX_TARGET", _DEFAULTS["NANVIX_TARGET"]))

    @property
    def machine(self) -> Machine:
        """Target machine identifier."""
        return Machine(self._data.get("NANVIX_MACHINE", _DEFAULTS["NANVIX_MACHINE"]))

    @property
    def deployment_mode(self) -> DeploymentMode:
        """Deployment mode."""
        return DeploymentMode(
            self._data.get(
                "NANVIX_DEPLOYMENT_MODE", _DEFAULTS["NANVIX_DEPLOYMENT_MODE"]
            )
        )

    @property
    def memory_size(self) -> MemorySize:
        """Memory size string."""
        return MemorySize(
            self._data.get("NANVIX_MEMORY_SIZE", _DEFAULTS["NANVIX_MEMORY_SIZE"])
        )

    # ------------------------------------------------------------------
    # Generic get / set
    # ------------------------------------------------------------------

    @overload
    def get(self, key: str, default: str) -> str: ...

    @overload
    def get(self, key: str, default: None = ...) -> str | None: ...

    def get(self, key: str, default: str | None = None) -> str | None:
        """Retrieve a configuration value.

        Environment variables always take precedence.

        Args:
            key: The configuration key.
            default: Value returned when the key is absent.

        Returns:
            The configuration value or *default*.
        """
        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val
        return self._data.get(key, default)

    def set(self, key: str, value: str) -> None:
        """Set a configuration value in memory.

        Call :meth:`save` to persist the change to disk.

        Args:
            key: The configuration key.
            value: The new value.
        """
        self._data[key] = value

    def delete(self, key: str) -> None:
        """Remove a configuration key from memory.

        Call :meth:`save` to persist the change to disk.

        Args:
            key: The configuration key to remove.
        """
        self._data.pop(key, None)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist the current in-memory configuration to ``.nanvix/env.json``."""
        with self._config_path.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)

    def load(self) -> None:
        """Reload configuration from ``.nanvix/env.json``.

        Missing or malformed files are silently ignored.
        """
        if not self._config_path.exists():
            return
        try:
            with self._config_path.open("r", encoding="utf-8") as fh:
                raw: object = json.load(fh)
            if not isinstance(raw, dict):
                return
            persisted = cast(dict[str, object], raw)
            for k, v in persisted.items():
                if isinstance(v, str):
                    # Environment variables still win.
                    if os.environ.get(k) is None:
                        self._data[k] = v
        except (json.JSONDecodeError, OSError):
            pass
