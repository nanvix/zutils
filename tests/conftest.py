# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Pytest bootstrap for the nanvix_zutil test suite.

The ``nanvix_zutil.paths`` module resolves ``.nanvix/`` by walking
up from the current working directory and aborts the process when no
such directory is found.  Since the test suite runs from the repo
root (which has no ``.nanvix/``), every test would crash at collection
time without an isolated working directory.

This file installs a single autouse fixture that:

* ``chdir``s into a fresh ``tmp_path`` containing an empty ``.nanvix/``,
* clears ``nanvix_root``'s cache before and after the test so that the
  next test (or any test that recreates ``.nanvix/`` content) gets a
  fresh resolution,
* leaves the original working directory restored by ``monkeypatch``.

Tests that need a manifest or other artifacts under ``.nanvix/`` should
write them into ``tmp_path / ".nanvix"`` themselves (or use the helpers
in ``tests.testutils``).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from nanvix_zutil.paths import nanvix_root


@pytest.fixture(autouse=True)
def _isolated_nanvix_root(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """Give every test a private ``.nanvix/`` directory in a fresh CWD."""
    (tmp_path / ".nanvix").mkdir()
    monkeypatch.chdir(tmp_path)
    nanvix_root.cache_clear()
    try:
        yield tmp_path
    finally:
        nanvix_root.cache_clear()
