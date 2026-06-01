# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

import re
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
"""Compiled regex matching a strict semver ``MAJOR.MINOR.PATCH`` string."""

# Constants
NANVIX_ROOT = Path(sys.prefix).parent
"""Path to the .nanvix directory"""

MANIFEST_PATH = NANVIX_ROOT / "nanvix.toml"
"""Path to the cross-compilation manifest."""

REPO_ROOT = NANVIX_ROOT.parent
"""Path to the repository root."""

OUT_DIR = NANVIX_ROOT / "out"
"""Path to the build output folder."""

BUILD_OUT = NANVIX_ROOT / "build"
"""Path to the built outputs to be distributed with ``release``."""

DIST_DIR = NANVIX_ROOT / "dist"
"""Path to the release output folder."""

LIB_OUT = BUILD_OUT / "lib"
"""Path to built library outputs to be bundled into the relase lib path"""

INCLUDE_OUT = BUILD_OUT / "include"
"""Path to built header outputs to be bundled into the release include path"""

BIN_OUT = BUILD_OUT / "bin"
"""Path to built binaries to be bundled into the release bin path."""

TEST_OUT = OUT_DIR / "test"
"""Path to built test binaries and ramfs images to be bundled for Windows tests."""

BUILDROOT = NANVIX_ROOT / "buildroot"
"""Path to the build root. Used to store items needed at build time."""

SYSROOT = NANVIX_ROOT / "sysroot"
"""Path to the sysroot. Used to store items needed a runtime."""
