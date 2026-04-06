# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Build matrix expansion and parallel execution for ``--all-builds``."""

from __future__ import annotations

import dataclasses
import itertools
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
