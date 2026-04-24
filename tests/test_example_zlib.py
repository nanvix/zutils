# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Integration tests for the hello-zlib example.

These tests exercise the example by invoking ``python -m nanvix_zutil``
from the example directory — the same way an end-user would run
``nanvix-zutil <command>``.

The full lifecycle test (setup → build → test → clean) requires the
Nanvix cross-compiler (``i686-nanvix-gcc``) installed natively on the
host, plus network access to download release assets from GitHub.
Detection order:

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

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "hello-zlib"


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


def _has_kvm() -> bool:
    """Return True if /dev/kvm is accessible (Linux only)."""
    if sys.platform != "linux":
        return False
    return os.access("/dev/kvm", os.R_OK | os.W_OK)


_HAS_TOOLCHAIN = _has_nanvix_toolchain()
_HAS_KVM = _has_kvm()
_SKIP_LIFECYCLE = (
    "Nanvix toolchain not available (set NANVIX_TOOLCHAIN or install to /opt/nanvix)"
)
_SKIP_NO_KVM = "KVM not available (/dev/kvm not accessible)"
_LIFECYCLE_TIMEOUT = (
    120  # seconds per step — setup downloads assets + build + VM boot + test + clean
)


class TestHelloZlibBash(unittest.TestCase):
    """End-to-end tests for the hello-zlib example via ``nanvix-zutil``."""

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
    # Lifecycle (requires toolchain + KVM + network)
    # ------------------------------------------------------------------

    @unittest.skip("TEMP - Need to update zlib first")
    @unittest.skipUnless(_HAS_TOOLCHAIN, _SKIP_LIFECYCLE)
    @unittest.skipUnless(_HAS_KVM, _SKIP_NO_KVM)
    def test_full_lifecycle(self) -> None:
        """Run setup → build → test → clean and verify each step."""
        # setup (downloads sysroot + zlib)
        r = self._run_z("setup")
        self.assertEqual(r.returncode, 0, r.stderr)

        # build
        r = self._run_z("build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_EXAMPLE_DIR / "hello-zlib.elf").exists(),
            "hello-zlib.elf should exist after build",
        )

        # test
        r = self._run_z("test")
        self.assertEqual(r.returncode, 0, r.stderr)

        # clean
        r = self._run_z("clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            (_EXAMPLE_DIR / "hello-zlib.elf").exists(),
            "hello-zlib.elf should not exist after clean",
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
        for artifact in ("hello-zlib.o", "hello-zlib.elf"):
            path = _EXAMPLE_DIR / artifact
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    unittest.main()
