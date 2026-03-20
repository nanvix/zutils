# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Standard configuration key names for the Nanvix build ecosystem.

Consumer build scripts use these constants to read/write values from
:class:`~nanvix_zutil.config.Config` without hard-coding key strings.
"""

# ---------------------------------------------------------------------------
# Config keys
# ---------------------------------------------------------------------------

CFG_SYSROOT: str = "NANVIX_SYSROOT"
"""Path to the downloaded Nanvix sysroot directory."""

CFG_TOOLCHAIN: str = "NANVIX_TOOLCHAIN"
"""Path to the Nanvix cross-compilation toolchain."""

CFG_TAG: str = "NANVIX_TAG"
"""Nanvix sysroot release tag to download."""

CFG_GH_TOKEN: str = "GH_TOKEN"
"""GitHub token for authenticated API requests (rate limits)."""
