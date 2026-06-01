# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Semver matching utilities shared across modules."""

import re

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
"""Compiled regex matching a strict semver ``MAJOR.MINOR.PATCH`` string."""
