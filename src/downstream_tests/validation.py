# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""validation.py -- Consumer name validation for downstream_tests."""

from __future__ import annotations

import re

CONSUMER_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")


def validate_consumer(name: str) -> bool:
    """Validate that *name* matches the ``owner/repo`` pattern.

    Rejects names that could cause shell injection or path traversal.

    Args:
        name: Consumer name to validate.

    Returns:
        True if valid, False otherwise.
    """
    if not CONSUMER_RE.match(name):
        return False
    # Reject path traversal components.
    parts = name.replace("\\", "/").split("/")
    if any(p in (".", "..") for p in parts):
        return False
    return True
