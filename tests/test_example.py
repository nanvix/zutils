# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Integration tests for the hello-world example.

These tests exercise the full bootstrap chain by invoking the example
through the actual ``z`` (Bash) and ``z.ps1`` (PowerShell) wrappers —
the same way an end-user would run ``./z <command>`` or ``./z.ps1 <command>``.

On the first run the bootstrap creates a virtualenv inside the example's
``.nanvix/venv/`` directory and installs ``nanvix_zutil`` from the local
source tree.  Subsequent runs reuse the cached environment.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "hello-world"
_Z_BASH = _EXAMPLE_DIR / "z"
_Z_PS1 = _EXAMPLE_DIR / "z.ps1"
_HAS_PWSH = shutil.which("pwsh") is not None
_HAS_BASH = shutil.which("bash") is not None


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
            timeout=120,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def test_full_lifecycle(self) -> None:
        """Run setup → build → test → clean and verify each step."""
        # setup
        r = self._run_z("setup")
        self.assertEqual(r.returncode, 0, r.stderr)

        # build
        r = self._run_z("build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_EXAMPLE_DIR / "build" / "hello.py").exists(),
            "build/ should contain hello.py after build",
        )

        # test
        r = self._run_z("test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Hello, World!", r.stdout)

        # clean
        r = self._run_z("clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            (_EXAMPLE_DIR / "build").exists(),
            "build/ should not exist after clean",
        )

    # ------------------------------------------------------------------
    # CLI flags
    # ------------------------------------------------------------------

    def test_help_returns_zero(self) -> None:
        """``--help`` exits successfully."""
        r = self._run_z("--help")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_json_mode(self) -> None:
        """``--json`` produces parseable JSON on stdout."""
        r = self._run_z("--json", "setup")
        self.assertEqual(r.returncode, 0, r.stderr)
        json_lines = [
            ln for ln in r.stdout.splitlines() if ln.startswith("{")
        ]
        self.assertTrue(json_lines, "expected at least one JSON line on stdout")
        for line in json_lines:
            obj: object = json.loads(line)
            self.assertIsInstance(obj, dict)
            assert isinstance(obj, dict)
            self.assertIn("level", obj)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove build artifacts left over from test runs."""
        build_dir = _EXAMPLE_DIR / "build"
        if build_dir.exists():
            shutil.rmtree(build_dir)


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
            timeout=120,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def test_full_lifecycle(self) -> None:
        """Run setup → build → test → clean via z.ps1."""
        # setup
        r = self._run_z_ps1("setup")
        self.assertEqual(r.returncode, 0, r.stderr)

        # build
        r = self._run_z_ps1("build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_EXAMPLE_DIR / "build" / "hello.py").exists(),
            "build/ should contain hello.py after build",
        )

        # test
        r = self._run_z_ps1("test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Hello, World!", r.stdout)

        # clean
        r = self._run_z_ps1("clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            (_EXAMPLE_DIR / "build").exists(),
            "build/ should not exist after clean",
        )

    # ------------------------------------------------------------------
    # CLI flags
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
        """``z.ps1 --json setup`` produces parseable JSON on stdout."""
        r = self._run_z_ps1("--json", "setup")
        self.assertEqual(r.returncode, 0, r.stderr)
        json_lines = [
            ln for ln in r.stdout.splitlines() if ln.startswith("{")
        ]
        self.assertTrue(json_lines, "expected at least one JSON line on stdout")
        for line in json_lines:
            obj: object = json.loads(line)
            self.assertIsInstance(obj, dict)
            assert isinstance(obj, dict)
            self.assertIn("level", obj)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove build artifacts left over from test runs."""
        build_dir = _EXAMPLE_DIR / "build"
        if build_dir.exists():
            shutil.rmtree(build_dir)


if __name__ == "__main__":
    unittest.main()
