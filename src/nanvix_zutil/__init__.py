# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""nanvix_zutil — Build orchestration utilities for the Nanvix ecosystem.

Public re-exports:

- :class:`~nanvix_zutil.script.ZScript` — base class for consumer build scripts
- :class:`~nanvix_zutil.config.Config` — persistent build configuration
- :class:`~nanvix_zutil.buildroot.Buildroot` — build-time dependency root
- :class:`~nanvix_zutil.buildroot.Dependency` — library dependency descriptor
- :class:`~nanvix_zutil.sysroot.Sysroot` — runtime sysroot management
- :mod:`nanvix_zutil.log` — structured logging helpers
- :mod:`nanvix_zutil.constants` — standard configuration key names
"""

from nanvix_zutil.buildroot import Buildroot, Dependency
from nanvix_zutil.config import Config
from nanvix_zutil.constants import CFG_GH_TOKEN, CFG_SYSROOT, CFG_TAG, CFG_TOOLCHAIN
from nanvix_zutil.script import ZScript
from nanvix_zutil.sysroot import Sysroot

__all__ = [
    "Buildroot",
    "CFG_GH_TOKEN",
    "CFG_SYSROOT",
    "CFG_TAG",
    "CFG_TOOLCHAIN",
    "Config",
    "Dependency",
    "Sysroot",
    "ZScript",
]
