"""validation.py — Consumer name validation for downstream_tests."""

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
    return bool(CONSUMER_RE.match(name))
