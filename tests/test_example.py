# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Integration tests for the lib-hello example.

These tests exercise the example by invoking ``python -m nanvix_zutil``
from the example directory — the same way an end-user would run
``nanvix-zutil <command>``.

The full lifecycle test (setup → build → test → clean) requires the
Nanvix cross-compiler (``i686-nanvix-gcc``) installed natively on the
host.  Detection order:

1. ``NANVIX_TOOLCHAIN`` environment variable
2. Default path ``/opt/nanvix/``

CLI flag tests (--help, --json) work without any external dependencies.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "lib-hello"


def _has_nanvix_toolchain() -> bool:
    """Return True if the Nanvix cross-compiler is available on the host.

    Only checks for a native ``i686-nanvix-gcc`` binary — Docker image
    availability is not sufficient because the lifecycle test invokes the
    compiler directly (not via ``--with-docker``).
    """
    custom = os.environ.get("NANVIX_TOOLCHAIN", "")
    if custom and Path(custom, "bin", "i686-nanvix-gcc").exists():
        return True
    if Path("/opt/nanvix/bin/i686-nanvix-gcc").exists():
        return True
    return False


_HAS_TOOLCHAIN = _has_nanvix_toolchain()
_SKIP_LIFECYCLE = (
    "Nanvix toolchain not available (set NANVIX_TOOLCHAIN or install to /opt/nanvix)"
)
_LIFECYCLE_TIMEOUT = 300  # seconds — setup + build + VM boot + test + clean


class TestLibHelloBash(unittest.TestCase):
    """End-to-end tests for the lib-hello example via ``nanvix-zutil``."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_z(*args: str) -> subprocess.CompletedProcess[str]:
        """Run the example via ``python -m nanvix_zutil``."""
        return subprocess.run(
            [sys.executable, "-m", "nanvix_zutil", *args],
            cwd=str(_EXAMPLE_DIR),
            capture_output=True,
            text=True,
            timeout=_LIFECYCLE_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Lifecycle (requires toolchain + network)
    # ------------------------------------------------------------------

    @unittest.skipUnless(_HAS_TOOLCHAIN, _SKIP_LIFECYCLE)
    def test_full_lifecycle(self) -> None:
        """Run setup → build → test → clean and verify each step."""
        with patch.dict(os.environ, {"NANVIX_DEPLOYMENT_MODE": "multi-process"}):
            self._run_lifecycle()

    def _run_lifecycle(self) -> None:
        """Execute the full setup → build → test → clean lifecycle."""
        # setup
        r = self._run_z("setup")
        self.assertEqual(r.returncode, 0, r.stderr)

        # build
        r = self._run_z("build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_EXAMPLE_DIR / "libhello.a").exists(),
            "libhello.a should exist after build",
        )

        # test
        r = self._run_z("test")
        self.assertEqual(r.returncode, 0, r.stderr)

        # clean
        r = self._run_z("clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            (_EXAMPLE_DIR / "libhello.a").exists(),
            "libhello.a should not exist after clean",
        )

    # ------------------------------------------------------------------
    # CLI flags (no toolchain required)
    # ------------------------------------------------------------------

    def test_help_returns_zero(self) -> None:
        """``--help`` exits successfully."""
        r = self._run_z("--help")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_json_mode(self) -> None:
        """``--json`` produces parseable JSON on stderr."""
        r = self._run_z("--json", "distclean")
        self.assertEqual(r.returncode, 0, r.stderr)
        json_lines = [ln for ln in r.stderr.splitlines() if ln.startswith("{")]
        self.assertTrue(json_lines, "expected at least one JSON line on stderr")
        for line in json_lines:
            obj: object = json.loads(line)
            self.assertIsInstance(obj, dict)
            assert isinstance(obj, dict)
            typed = cast(dict[str, object], obj)
            self.assertIn("level", typed)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove build artifacts left over from test runs."""
        for artifact in ("hello.o", "libhello.a"):
            path = _EXAMPLE_DIR / artifact
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    unittest.main()
