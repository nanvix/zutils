# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Integration tests for the hello-world example.

These tests exercise the bootstrap chain by invoking the example through
the actual ``z`` (Bash) and ``z.ps1`` (PowerShell) wrappers — the same
way an end-user would run ``./z <command>``.

The full lifecycle test (setup → build → test → clean) requires the
Nanvix cross-compiler (``i686-nanvix-gcc``).  Detection order:

1. ``NANVIX_TOOLCHAIN`` environment variable
2. Default path ``/opt/nanvix/``
3. Docker image ``nanvix/toolchain:latest-minimal``

CLI flag tests (--help, --json) work without any external dependencies.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "hello-world"
_Z_BASH = _EXAMPLE_DIR / "z"
_Z_PS1 = _EXAMPLE_DIR / "z.ps1"
_HAS_PWSH = shutil.which("pwsh") is not None
_HAS_BASH = shutil.which("bash") is not None


def _has_nanvix_toolchain() -> bool:
    """Return True if the Nanvix cross-compiler is available."""
    custom = os.environ.get("NANVIX_TOOLCHAIN", "")
    if custom and Path(custom, "bin", "i686-nanvix-gcc").exists():
        return True
    if Path("/opt/nanvix/bin/i686-nanvix-gcc").exists():
        return True
    if shutil.which("docker"):
        result = subprocess.run(
            ["docker", "image", "inspect", "nanvix/toolchain:latest-minimal"],
            capture_output=True,
        )
        return result.returncode == 0
    return False


def _has_kvm() -> bool:
    """Return True if /dev/kvm is accessible."""
    return os.access("/dev/kvm", os.R_OK | os.W_OK)


_HAS_TOOLCHAIN = _has_nanvix_toolchain()
_HAS_KVM = _has_kvm()
_SKIP_LIFECYCLE = "Nanvix toolchain not available (set NANVIX_TOOLCHAIN, install to /opt/nanvix, or pull Docker image)"
_SKIP_NO_KVM = "KVM not available (/dev/kvm not accessible)"
_LIFECYCLE_TIMEOUT = 300  # seconds — setup + build + VM boot + test + clean


@unittest.skipUnless(_HAS_BASH, "bash not found in PATH")
class TestHelloWorldBash(unittest.TestCase):
    """End-to-end tests for the ``z`` (Bash) bootstrap wrapper."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_z(*args: str) -> subprocess.CompletedProcess[str]:
        """Run the example via ``bash z`` with the given arguments."""
        return subprocess.run(
            ["bash", str(_Z_BASH), *args],
            cwd=str(_EXAMPLE_DIR),
            capture_output=True,
            text=True,
            timeout=_LIFECYCLE_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Lifecycle (requires toolchain + KVM + network)
    # ------------------------------------------------------------------

    @unittest.skipUnless(_HAS_TOOLCHAIN, _SKIP_LIFECYCLE)
    @unittest.skipUnless(_HAS_KVM, _SKIP_NO_KVM)
    def test_full_lifecycle(self) -> None:
        """Run setup → build → test → clean and verify each step."""
        # setup
        r = self._run_z("setup")
        self.assertEqual(r.returncode, 0, r.stderr)

        # build
        r = self._run_z("build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_EXAMPLE_DIR / "hello.elf").exists(),
            "hello.elf should exist after build",
        )

        # test
        r = self._run_z("test")
        self.assertEqual(r.returncode, 0, r.stderr)

        # clean
        r = self._run_z("clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            (_EXAMPLE_DIR / "hello.elf").exists(),
            "hello.elf should not exist after clean",
        )

    # ------------------------------------------------------------------
    # CLI flags (no toolchain required)
    # ------------------------------------------------------------------

    def test_help_returns_zero(self) -> None:
        """``--help`` exits successfully."""
        r = self._run_z("--help")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_json_mode(self) -> None:
        """``--json`` produces parseable JSON on stdout."""
        r = self._run_z("--json", "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        json_lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(json_lines, "expected at least one JSON line on stdout")
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
        for artifact in ("hello.o", "hello.elf"):
            path = _EXAMPLE_DIR / artifact
            if path.exists():
                path.unlink()


@unittest.skipUnless(_HAS_PWSH, "pwsh (PowerShell) not found in PATH")
class TestHelloWorldPS1(unittest.TestCase):
    """End-to-end tests for the ``z.ps1`` bootstrap wrapper."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_z_ps1(*args: str) -> subprocess.CompletedProcess[str]:
        """Run the example via ``pwsh z.ps1`` with the given arguments."""
        return subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(_Z_PS1), *args],
            cwd=str(_EXAMPLE_DIR),
            capture_output=True,
            text=True,
            timeout=_LIFECYCLE_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Lifecycle (requires toolchain + KVM + network)
    # ------------------------------------------------------------------

    @unittest.skipUnless(_HAS_TOOLCHAIN, _SKIP_LIFECYCLE)
    @unittest.skipUnless(_HAS_KVM, _SKIP_NO_KVM)
    def test_full_lifecycle(self) -> None:
        """Run setup → build → test → clean via z.ps1."""
        # setup
        r = self._run_z_ps1("setup")
        self.assertEqual(r.returncode, 0, r.stderr)

        # build
        r = self._run_z_ps1("build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_EXAMPLE_DIR / "hello.elf").exists(),
            "hello.elf should exist after build",
        )

        # test
        r = self._run_z_ps1("test")
        self.assertEqual(r.returncode, 0, r.stderr)

        # clean
        r = self._run_z_ps1("clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            (_EXAMPLE_DIR / "hello.elf").exists(),
            "hello.elf should not exist after clean",
        )

    # ------------------------------------------------------------------
    # CLI flags (no toolchain required)
    # ------------------------------------------------------------------

    def test_help_returns_zero(self) -> None:
        """``z.ps1 --help`` exits successfully."""
        r = self._run_z_ps1("--help")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_args_shows_help(self) -> None:
        """``z.ps1`` with no arguments prints help and exits 0."""
        r = self._run_z_ps1()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage:", r.stdout)

    def test_json_mode(self) -> None:
        """``z.ps1 --json clean`` produces parseable JSON on stdout."""
        r = self._run_z_ps1("--json", "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        json_lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(json_lines, "expected at least one JSON line on stdout")
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
        for artifact in ("hello.o", "hello.elf"):
            path = _EXAMPLE_DIR / artifact
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    unittest.main()
