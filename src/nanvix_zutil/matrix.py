# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Build matrix expansion and parallel execution for ``--all-builds``."""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanvix_zutil.manifest import BuildMatrix

from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DIMENSION_ENV_MAP: dict[str, str] = {
    "platform": "NANVIX_MACHINE",
    "mode": "NANVIX_DEPLOYMENT_MODE",
    "memory": "NANVIX_MEMORY_SIZE",
}
"""Mapping from build dimension names to their corresponding environment variables."""

_HOOK_CHAIN: dict[str, tuple[str, ...]] = {
    "setup": ("setup",),
    "build": ("setup", "build"),
    "test": ("setup", "build", "test"),
    "benchmark": ("setup", "build", "benchmark"),
    "release": ("release",),
    "clean": ("clean",),
    "distclean": ("distclean",),
}
"""Prerequisite hook chains for each lifecycle subcommand."""

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BuildCombo:
    """A single (platform, mode, memory) build configuration."""

    platform: str
    mode: str
    memory: str


@dataclasses.dataclass
class BuildResult:
    """Outcome of running a lifecycle hook for one :class:`BuildCombo`."""

    combo: BuildCombo
    success: bool
    duration_seconds: float
    error: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def expand_matrix(builds: BuildMatrix) -> list[BuildCombo]:
    """Expand a :class:`~nanvix_zutil.manifest.BuildMatrix` into all valid combos.

    Computes the cross-product of the ``platforms``, ``modes``, and ``memory``
    dimensions, then removes any combo that matches an entry in
    ``builds.exclude``.  A combo is excluded when the exclude dict is a
    subset of the combo's fields (partial-match semantics).

    Args:
        builds: Parsed build matrix from ``nanvix.toml``.

    Returns:
        Sorted list of :class:`BuildCombo` instances for deterministic ordering.
    """
    for key in ("platforms", "modes", "memory"):
        if key not in builds.dimensions:
            log.fatal(
                f"[builds] matrix is missing required dimension '{key}'",
                code=EXIT_INVALID_ARGS,
            )

    platforms = builds.dimensions["platforms"]
    modes = builds.dimensions["modes"]
    memories = builds.dimensions["memory"]

    combos: list[BuildCombo] = []
    for platform, mode, memory in itertools.product(platforms, modes, memories):
        combo = BuildCombo(platform=platform, mode=mode, memory=memory)
        combo_fields = dataclasses.asdict(combo)
        excluded = any(
            all(combo_fields.get(k) == v for k, v in exclude.items())
            for exclude in builds.exclude
        )
        if not excluded:
            combos.append(combo)

    return sorted(combos, key=lambda c: (c.platform, c.mode, c.memory))


def filter_matrix(
    combos: list[BuildCombo],
    *,
    mode: str | None = None,
) -> list[BuildCombo]:
    """Filter a list of build combos by deployment mode.

    Args:
        combos: Full list of :class:`BuildCombo` instances to filter.
        mode: If not ``None``, keep only combos whose ``mode`` field equals
            this value.

    Returns:
        Filtered list of :class:`BuildCombo` instances.
    """
    if mode is None:
        return combos
    return [c for c in combos if c.mode == mode]


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------

#: Lock used to guard the env-set → ZScript-instantiate → env-restore sequence
#: in :func:`run_all_builds` workers so that concurrent threads do not clobber
#: each other's ``os.environ`` values.
_env_lock: threading.Lock = threading.Lock()


def run_all_builds(
    script_cls: type,
    combos: list[BuildCombo],
    hook: str,
    targets: list[str],
    docker_image: str | None,
    repo_root: Path,
) -> dict[BuildCombo, BuildResult]:
    """Run *hook* for every combo in *combos* using a thread pool.

    Each worker sets the appropriate environment variables, instantiates
    *script_cls*, chains prerequisite hooks, and runs the target hook.

    Args:
        script_cls: The :class:`~nanvix_zutil.ZScript` subclass to instantiate.
        combos: Build combinations to execute.
        hook: Lifecycle hook name (``"setup"``, ``"build"``, ``"test"``, etc.).
        targets: Arguments passed after ``--`` on the command line.
        docker_image: Docker image to use, or ``None`` for host execution.
        repo_root: Path to the consumer repository root.

    Returns:
        Mapping from each combo to its :class:`BuildResult`.
    """
    # Determine which hooks to run in sequence for this subcommand.
    hook_chain: tuple[str, ...] = _HOOK_CHAIN.get(hook, (hook,))

    max_workers = min(len(combos), os.cpu_count() or 4)

    def _run_combo(combo: BuildCombo) -> BuildResult:
        """Worker: set env, instantiate script, run hook chain."""
        start = time.monotonic()

        # Capture the original values of the env vars we are about to set
        # so they can be restored after instantiation.
        env_keys = list(_DIMENSION_ENV_MAP.values())
        saved: dict[str, str | None] = {k: os.environ.get(k) for k in env_keys}

        try:
            # --- Lock-guarded: set env → instantiate → restore env ----------
            with _env_lock:
                os.environ[_DIMENSION_ENV_MAP["platform"]] = combo.platform
                os.environ[_DIMENSION_ENV_MAP["mode"]] = combo.mode
                os.environ[_DIMENSION_ENV_MAP["memory"]] = combo.memory

                # Instantiation reads env vars via Config.__init__; the
                # captured Config object retains its values after restore.
                instance = script_cls(repo_root)

            instance.targets = targets

            # Restore env vars so other threads are not affected.
            for key, original in saved.items():
                if original is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original

            # Docker setup — mirror the logic in ZScript.main().
            if docker_image is not None:
                from nanvix_zutil.docker import docker_available, image_exists

                if not docker_available():
                    return BuildResult(
                        combo=combo,
                        success=False,
                        duration_seconds=time.monotonic() - start,
                        error="Docker is not available",
                    )
                if not image_exists(docker_image):
                    return BuildResult(
                        combo=combo,
                        success=False,
                        duration_seconds=time.monotonic() - start,
                        error=f"Docker image '{docker_image}' not found locally",
                    )
                instance.docker = instance.docker_config(docker_image)

            # Run prerequisite chain.
            for step in hook_chain:
                method = getattr(instance, step, None)
                if callable(method):
                    method()

        except SystemExit as exc:
            elapsed = time.monotonic() - start
            code = exc.code if exc.code is not None else 1
            return BuildResult(
                combo=combo,
                success=False,
                duration_seconds=elapsed,
                error=f"Exited with code {code}",
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - start
            return BuildResult(
                combo=combo,
                success=False,
                duration_seconds=elapsed,
                error=str(exc),
            )
        else:
            # Restore env vars in case of early return paths that bypass
            # the restore above (defensive).
            for key, original in saved.items():
                if original is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original

        return BuildResult(
            combo=combo,
            success=True,
            duration_seconds=time.monotonic() - start,
        )

    results: dict[BuildCombo, BuildResult] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_combo = {
            executor.submit(_run_combo, combo): combo for combo in combos
        }
        for future in as_completed(future_to_combo):
            result = future.result()
            results[result.combo] = result

    return results


def print_summary(results: dict[BuildCombo, BuildResult]) -> None:
    """Print a tabular summary of build results to stderr.

    In plain-text mode an ASCII table is printed via :func:`~nanvix_zutil.log.info`.
    In JSON mode one JSON object per result is emitted instead.

    Args:
        results: Mapping from :class:`BuildCombo` to :class:`BuildResult` as
            returned by :func:`run_all_builds`.
    """
    if log.is_json_mode():
        for result in results.values():
            obj: dict[str, object] = {
                "platform": result.combo.platform,
                "mode": result.combo.mode,
                "memory": result.combo.memory,
                "result": "PASS" if result.success else "FAIL",
                "duration_seconds": round(result.duration_seconds, 2),
            }
            if result.error is not None:
                obj["error"] = result.error
            log.info(json.dumps(obj))
        return

    # --- ASCII table ---------------------------------------------------------
    def _fmt_duration(seconds: float) -> str:
        """Format a duration as Xm Ys or Xs."""
        total = int(seconds)
        minutes, secs = divmod(total, 60)
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    rows: list[tuple[str, str, str, str, str]] = []
    for result in results.values():
        status = "PASS" if result.success else "FAIL"
        rows.append(
            (
                result.combo.platform,
                result.combo.mode,
                result.combo.memory,
                status,
                _fmt_duration(result.duration_seconds),
            )
        )

    headers = ("Platform", "Mode", "Memory", "Result", "Time")
    col_widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]

    def _row_line(cells: tuple[str, ...]) -> str:
        parts = [f" {cell:<{col_widths[i]}} " for i, cell in enumerate(cells)]
        return "|" + "|".join(parts) + "|"

    def _separator() -> str:
        parts = ["-" * (col_widths[i] + 2) for i in range(len(headers))]
        return "+" + "+".join(parts) + "+"

    sep = _separator()
    log.info(sep)
    log.info(_row_line(headers))
    log.info(sep)
    for row in rows:
        log.info(_row_line(row))
    log.info(sep)
