# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Integration tests for the example projects (lib-hello and bin-hello).

These tests exercise both examples end-to-end via ``nanvix-zutil``,
the same way a real user would.

**CI Linux** (Docker available):
    Full lifecycle — ``setup → build → test`` — for both examples.

**CI Windows** (pre-built artifacts downloaded from Linux job):
    Test-only — ``nanvix-zutil test`` — for both examples.

**Local** (no Docker):
    CLI smoke tests only (``--help``, ``--json``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIB_HELLO = _REPO_ROOT / "examples" / "lib-hello"
_BIN_HELLO = _REPO_ROOT / "examples" / "bin-hello"
_DOCKER_IMAGE = "nanvix/toolchain:latest-minimal"
_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _has_docker() -> bool:
    """Return True if the Docker daemon is reachable."""
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _has_docker_image() -> bool:
    """Return True if the toolchain Docker image is already pulled."""
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", _DOCKER_IMAGE],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_HAS_DOCKER = _has_docker()
_SKIP_NO_DOCKER = "Docker daemon not available"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run_z(
    cwd: Path, *args: str, timeout: int = _TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Invoke ``nanvix-zutil`` in *cwd* and return the completed process."""
    return subprocess.run(
        [sys.executable, "-m", "nanvix_zutil", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _pull_docker_image() -> None:
    """Pull the toolchain Docker image if not already present."""
    if not _has_docker_image():
        subprocess.run(["docker", "pull", _DOCKER_IMAGE], check=True, timeout=_TIMEOUT)


def _setup_bin_hello_buildroot() -> None:
    """Populate bin-hello's buildroot from lib-hello artifacts.

    bin-hello declares ``lib-hello`` as a dependency, but the GitHub
    repo ``nanvix/lib-hello`` does not exist.  This helper reuses the
    sysroot and build artifacts produced by the lib-hello lifecycle test.
    """
    nanvix_dir = _BIN_HELLO / ".nanvix"
    sysroot_src = (_LIB_HELLO / ".nanvix" / "sysroot").resolve()
    sysroot_dst = nanvix_dir / "sysroot"
    if not sysroot_dst.exists():
        sysroot_dst.symlink_to(sysroot_src)

    buildroot = nanvix_dir / "buildroot"
    (buildroot / "lib").mkdir(parents=True, exist_ok=True)
    (buildroot / "include").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_LIB_HELLO / "libhello.a", buildroot / "lib" / "libhello.a")
    shutil.copy2(_LIB_HELLO / "src" / "hello.h", buildroot / "include" / "hello.h")

    cfg = {
        "NANVIX_TARGET": "x86",
        "NANVIX_MACHINE": "microvm",
        "NANVIX_DEPLOYMENT_MODE": "standalone",
        "NANVIX_MEMORY_SIZE": "256mb",
        "NANVIX_SYSROOT": str(sysroot_src),
        "NANVIX_TOOLCHAIN": "/opt/nanvix",
        "NANVIX_DOCKER_IMAGE": _DOCKER_IMAGE,
    }
    nanvix_dir.mkdir(parents=True, exist_ok=True)
    (nanvix_dir / "env.json").write_text(json.dumps(cfg, indent=2))


# ===================================================================
# CLI smoke tests (always run — no Docker or toolchain needed)
# ===================================================================


class TestLibHelloCLI(unittest.TestCase):
    """CLI flag tests for the lib-hello example."""

    def test_help_returns_zero(self) -> None:
        """``--help`` exits successfully."""
        r = _run_z(_LIB_HELLO, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_json_mode(self) -> None:
        """``--json`` produces parseable JSON on stderr."""
        r = _run_z(_LIB_HELLO, "--json", "distclean")
        self.assertEqual(r.returncode, 0, r.stderr)
        json_lines = [ln for ln in r.stderr.splitlines() if ln.startswith("{")]
        self.assertTrue(json_lines, "expected at least one JSON line on stderr")
        for line in json_lines:
            obj: object = json.loads(line)
            self.assertIsInstance(obj, dict)
            assert isinstance(obj, dict)
            typed = cast(dict[str, object], obj)
            self.assertIn("level", typed)


class TestBinHelloCLI(unittest.TestCase):
    """CLI flag tests for the bin-hello example."""

    def test_help_returns_zero(self) -> None:
        """``--help`` exits successfully."""
        r = _run_z(_BIN_HELLO, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_json_mode(self) -> None:
        """``--json`` produces parseable JSON on stderr."""
        r = _run_z(_BIN_HELLO, "--json", "distclean")
        self.assertEqual(r.returncode, 0, r.stderr)
        json_lines = [ln for ln in r.stderr.splitlines() if ln.startswith("{")]
        self.assertTrue(json_lines, "expected at least one JSON line on stderr")
        for line in json_lines:
            obj: object = json.loads(line)
            self.assertIsInstance(obj, dict)
            assert isinstance(obj, dict)
            typed = cast(dict[str, object], obj)
            self.assertIn("level", typed)


# ===================================================================
# Full lifecycle tests (require Docker)
# ===================================================================


@unittest.skipUnless(_HAS_DOCKER, _SKIP_NO_DOCKER)
class TestLibHelloLifecycle(unittest.TestCase):
    """setup → build → test for the lib-hello example."""

    @classmethod
    def setUpClass(cls) -> None:
        _pull_docker_image()

    def test_full_lifecycle(self) -> None:
        """Run setup → build → test and verify each step."""
        # setup
        r = _run_z(_LIB_HELLO, "setup", "--with-docker")
        self.assertEqual(r.returncode, 0, r.stderr)

        # build
        r = _run_z(_LIB_HELLO, "build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_LIB_HELLO / "libhello.a").exists(),
            "libhello.a should exist after build",
        )

        # test
        r = _run_z(_LIB_HELLO, "test")
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(_HAS_DOCKER, _SKIP_NO_DOCKER)
class TestBinHelloLifecycle(unittest.TestCase):
    """setup → build → test for the bin-hello example.

    Depends on lib-hello being built first (same pytest session).
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not (_LIB_HELLO / "libhello.a").exists():
            raise unittest.SkipTest("lib-hello not built — lifecycle tests failed?")
        if not (_LIB_HELLO / ".nanvix" / "sysroot").exists():
            raise unittest.SkipTest("lib-hello sysroot not available")
        _setup_bin_hello_buildroot()

    def test_full_lifecycle(self) -> None:
        """Run build → test and verify each step."""
        # build
        r = _run_z(_BIN_HELLO, "build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_BIN_HELLO / "hello.elf").exists(),
            "hello.elf should exist after build",
        )

        # test
        r = _run_z(_BIN_HELLO, "test")
        self.assertEqual(r.returncode, 0, r.stderr)


# ===================================================================
# Test-only (pre-built artifacts — Windows CI or manual testing)
#
# Skipped when Docker is available because the lifecycle tests above
# already cover testing.  On Windows CI (no Docker), these run against
# artifacts downloaded from the Linux job.
# ===================================================================


@unittest.skipIf(_HAS_DOCKER, "covered by lifecycle tests above")
class TestLibHelloTestOnly(unittest.TestCase):
    """Run ``nanvix-zutil test`` against pre-built lib-hello artifacts."""

    def test_lib_hello(self) -> None:
        if not (_LIB_HELLO / "libhello.a").exists():
            self.skipTest("libhello.a not found — build first or download artifacts")
        r = _run_z(_LIB_HELLO, "test")
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipIf(_HAS_DOCKER, "covered by lifecycle tests above")
class TestBinHelloTestOnly(unittest.TestCase):
    """Run ``nanvix-zutil test`` against pre-built bin-hello artifacts."""

    def test_bin_hello(self) -> None:
        if not (_BIN_HELLO / "hello.elf").exists():
            self.skipTest("hello.elf not found — build first or download artifacts")
        r = _run_z(_BIN_HELLO, "test")
        self.assertEqual(r.returncode, 0, r.stderr)


# ===================================================================
# Cleanup
# ===================================================================


@unittest.skipUnless(_HAS_DOCKER, "nothing to clean")
class TestExamplesCleanup(unittest.TestCase):
    """Clean both examples after lifecycle tests.

    Runs only when Docker is available (``clean`` is a Docker command).
    On CI, this runs *after* artifact upload because the upload step
    is an ``if: success()`` GitHub Actions step placed between the
    pytest invocation and this cleanup — achieved by splitting the
    pytest run: ``-k "not Cleanup"`` first, then ``-k Cleanup``.
    """

    def test_clean_lib_hello(self) -> None:
        r = _run_z(_LIB_HELLO, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            (_LIB_HELLO / "libhello.a").exists(),
            "libhello.a should not exist after clean",
        )

    def test_clean_bin_hello(self) -> None:
        r = _run_z(_BIN_HELLO, "clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            (_BIN_HELLO / "hello.elf").exists(),
            "hello.elf should not exist after clean",
        )


if __name__ == "__main__":
    unittest.main()
