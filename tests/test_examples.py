# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Integration tests for the example projects (lib-hello and bin-hello).

These tests exercise both examples end-to-end **through the bootstrap
wrapper** (``z.sh`` / ``z.ps1``), the same way a real user would.  The
wrapper is pointed at the in-tree source via ``--with-zutils <repo root>``,
which performs an editable install into ``examples/<name>/.nanvix/venv/``
and bypasses the pinned-wheel version check.  This means the bootstrap
logic itself is covered by these tests, not just the underlying CLI.

**CI Linux** (Docker available):
    Full lifecycle — ``setup → build → test`` — for both examples.

**CI Windows** (pre-built artifacts downloaded from Linux job):
    Test-only — ``nanvix-zutil test`` — for both examples.  Requires
    ``pwsh`` on PATH.

**Local** (no Docker):
    CLI smoke tests only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIB_HELLO = _REPO_ROOT / "examples" / "lib-hello"
_BIN_HELLO = _REPO_ROOT / "examples" / "bin-hello"
_DOCKER_IMAGE = "ghcr.io/nanvix/toolchain-gcc:sha-34a3641"
_NANVIX_VERSION = "0.13.19"
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


def _can_run_docker_lifecycle() -> bool:
    """Return True if Docker lifecycle tests can run.

    Requires the Docker daemon **and** a platform that can execute
    Linux containers.  Windows CI has Docker Desktop but only supports
    Windows containers, so the Linux toolchain image cannot run there.
    """
    if sys.platform == "win32":
        return False
    return _has_docker()


_CAN_LIFECYCLE = _can_run_docker_lifecycle()
_SKIP_NO_DOCKER = "Docker lifecycle not available (no daemon or Windows host)"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run_z(
    cwd: Path,
    *args: str,
    timeout: int = _TIMEOUT,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the bootstrap wrapper in *cwd* and return the completed process.

    Calls ``z.sh`` (Linux/macOS) or ``z.ps1`` via ``pwsh`` (Windows) with
    ``--with-zutils <repo root>`` prepended, so the wrapper materialises
    an editable install of ``nanvix-zutil`` into ``cwd/.nanvix/venv/`` and
    dispatches the subcommand through that venv.  The first call per
    example dir pays the editable-install cost (~5-30s); subsequent calls
    hit the recorded-path fast path.
    """
    env = os.environ.copy()
    # An outer venv must not bleed into the wrapper's `python -m venv`
    # invocation (`pip install -e` would otherwise target the outer venv
    # when the wrapper's resolved python happens to be `sys.executable`).
    env.pop("VIRTUAL_ENV", None)
    if extra_env:
        env.update(extra_env)
    z_args = ("--with-zutils", str(_REPO_ROOT), *args)
    if sys.platform == "win32":
        cmd = ["pwsh", "-NoProfile", "-File", str(cwd / "z.ps1"), *z_args]
    else:
        cmd = [str(cwd / "z.sh"), *z_args]
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _pull_docker_image() -> None:
    """Pull the toolchain Docker image if not already present."""
    if not _has_docker_image():
        subprocess.run(["docker", "pull", _DOCKER_IMAGE], check=True, timeout=_TIMEOUT)


def _write_env_json(nanvix_dir: Path, sysroot: Path) -> None:
    """Write env.json so nanvix-zutil build/test/clean can find paths."""
    cfg = {
        "NANVIX_TARGET": "x86",
        "NANVIX_MACHINE": "microvm",
        "NANVIX_DEPLOYMENT_MODE": "standalone",
        "NANVIX_MEMORY_SIZE": "256mb",
        "NANVIX_SYSROOT": str(sysroot),
        "NANVIX_DOCKER_IMAGE": _DOCKER_IMAGE,
    }
    nanvix_dir.mkdir(parents=True, exist_ok=True)
    (nanvix_dir / "env.json").write_text(json.dumps(cfg, indent=2))


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

    _write_env_json(nanvix_dir, sysroot_src)


def _setup_windows_sysroot() -> Path:
    """Download sysroot and Windows host binaries for test-only runs.

    On Windows CI, build artifacts are downloaded from the Linux job but
    the sysroot is not included.  This helper downloads the sysroot and
    Windows-native binaries (``nanvixd.exe``, etc.) so that functional
    tests can run.

    Returns the resolved sysroot path.
    """
    from nanvix_zutil.sysroot import Sysroot

    sysroot_dir = _LIB_HELLO / ".nanvix" / "sysroot"
    gh_token = os.environ.get("GH_TOKEN")

    sysroot = Sysroot.download(
        machine="microvm",
        deployment_mode="standalone",
        memory_size="256mb",
        tag=f"v{_NANVIX_VERSION}",
        gh_token=gh_token,
        dest=sysroot_dir,
    )
    sysroot.download_windows_binaries(
        machine="microvm",
        deployment_mode="standalone",
        memory_size="256mb",
        gh_token=gh_token,
    )
    return sysroot.path


# ===================================================================
# CLI smoke tests (always run — no Docker or toolchain needed)
# ===================================================================


class TestLibHelloCLI(unittest.TestCase):
    """CLI flag tests for the lib-hello example."""

    def test_help_returns_zero(self) -> None:
        """``--help`` exits successfully."""
        r = _run_z(_LIB_HELLO, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestBinHelloCLI(unittest.TestCase):
    """CLI flag tests for the bin-hello example."""

    def test_help_returns_zero(self) -> None:
        """``--help`` exits successfully."""
        r = _run_z(_BIN_HELLO, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)


# ===================================================================
# Full lifecycle tests (require Docker)
# ===================================================================


@unittest.skipUnless(_CAN_LIFECYCLE, _SKIP_NO_DOCKER)
class TestLibHelloLifecycle(unittest.TestCase):
    """setup → build → test for the lib-hello example."""

    @classmethod
    def setUpClass(cls) -> None:
        _pull_docker_image()

    def test_full_lifecycle(self) -> None:
        """Run setup → build → test and verify each step."""
        # setup
        r = _run_z(_LIB_HELLO, "setup", "--with-docker", _DOCKER_IMAGE)
        self.assertEqual(r.returncode, 0, r.stderr)

        # build
        r = _run_z(_LIB_HELLO, "build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            (_LIB_HELLO / "libhello.a").exists(),
            "libhello.a should exist after build",
        )
        self.assertIn("Build complete", r.stderr)

        # test
        r = _run_z(_LIB_HELLO, "test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("is a valid ar archive", r.stderr)
        self.assertIn("Test complete", r.stderr)


@unittest.skipUnless(_CAN_LIFECYCLE, _SKIP_NO_DOCKER)
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
        self.assertIn("Build complete", r.stderr)

        # test
        r = _run_z(_BIN_HELLO, "test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("is a valid ELF binary", r.stderr)
        # Functional tests require Docker which the test subcommand
        # does not enable; only smoke + integration assertions here.
        self.assertIn("Test complete", r.stderr)


# ===================================================================
# Test-only (pre-built artifacts — Windows CI or manual testing)
#
# Skipped when Docker is available because the lifecycle tests above
# already cover testing.  On Windows CI (no Docker), these run against
# artifacts downloaded from the Linux job.
# ===================================================================


@unittest.skipIf(_CAN_LIFECYCLE, "covered by lifecycle tests above")
class TestLibHelloTestOnly(unittest.TestCase):
    """Run ``nanvix-zutil test`` against pre-built lib-hello artifacts."""

    def test_lib_hello(self) -> None:
        if not (_LIB_HELLO / "libhello.a").exists():
            self.skipTest("libhello.a not found — build first or download artifacts")
        r = _run_z(_LIB_HELLO, "test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("is a valid ar archive", r.stderr)
        self.assertIn("Test complete", r.stderr)


@unittest.skipIf(_CAN_LIFECYCLE, "covered by lifecycle tests above")
class TestBinHelloTestOnly(unittest.TestCase):
    """Run ``nanvix-zutil test`` against pre-built bin-hello artifacts.

    On Windows, the sysroot is downloaded so that functional tests run
    under ``nanvixd.exe`` rather than being silently skipped.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not (_BIN_HELLO / "hello.elf").exists():
            raise unittest.SkipTest(
                "hello.elf not found — build first or download artifacts"
            )
        # Download sysroot + Windows binaries so functional tests can run.
        if sys.platform == "win32":
            sysroot = _setup_windows_sysroot()
            _write_env_json(_BIN_HELLO / ".nanvix", sysroot)

    def test_bin_hello(self) -> None:
        r = _run_z(_BIN_HELLO, "test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("is a valid ELF binary", r.stderr)
        # On Windows, nanvixd.exe runs natively so functional tests execute.
        if sys.platform == "win32":
            self.assertIn("PASS: bin-hello functional tests", r.stderr)
        self.assertIn("Test complete", r.stderr)


# ===================================================================
# Cleanup
# ===================================================================


class TestExamplesCleanup(unittest.TestCase):
    """Clean both examples after lifecycle tests.

    Runs only when both examples have a ``.nanvix/env.json`` written by
    a prior ``setup`` invocation and the current environment can still
    execute the lifecycle. This decouples the two pytest invocations
    used in CI (``-k "not Cleanup"`` then ``-k Cleanup``): if the first
    invocation skipped the lifecycle tests for any reason, or Docker is
    unavailable in the current run, cleanup is skipped as well instead
    of failing with "no Docker image configured".
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not _CAN_LIFECYCLE:
            raise unittest.SkipTest(
                "nothing to clean — lifecycle is unavailable in this environment"
            )
        if not (
            (_LIB_HELLO / ".nanvix" / "env.json").exists()
            and (_BIN_HELLO / ".nanvix" / "env.json").exists()
        ):
            raise unittest.SkipTest("nothing to clean — setup did not run")

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
