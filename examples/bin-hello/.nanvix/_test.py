# This module exists mostly to show off local imports.
# Conventional _ prefix to indicate a private module,
# and to prevent collisions with stdlib

import sys
from pathlib import Path, PurePosixPath

from nanvix_zutil import (
    CFG_SYSROOT,
    EXIT_BUILD_FAILURE,
    ZScript,
    log,
)
from nanvix_zutil.exitcodes import EXIT_TEST_FAILURE
from nanvix_zutil.helpers import run
from nanvix_zutil.paths import repo_root


class Test:
    def __init__(self, script: ZScript):
        self.script = script

    def test(self) -> None:
        """Run the test suite (smoke + integration + functional).

        The functional test phase runs under ``nanvixd.elf`` inside a
        Docker container on Linux, or natively under ``nanvixd.exe`` on
        Windows.  On Linux, functional tests are skipped when Docker is
        not configured (the ``test`` subcommand does not enable Docker
        automatically).
        """
        binary = repo_root() / "hello.elf"

        # Smoke: binary must exist and be non-trivially sized.
        log.info("=== bin-hello smoke tests ===")
        if not binary.exists():
            log.fatal(
                f"{binary} not found — run 'nanvix-zutil build' first.",
                code=EXIT_TEST_FAILURE,
            )
        size = binary.stat().st_size
        if size < 1000:
            log.fatal(f"{binary} too small ({size} bytes).", code=EXIT_TEST_FAILURE)
        log.success(f"OK: {binary.name} ({size} bytes)")

        # Integration: verify ELF magic.
        log.info("=== bin-hello integration tests ===")
        with binary.open("rb") as fh:
            magic = fh.read(4)
        if magic != b"\x7fELF":
            log.fatal(f"{binary} is not a valid ELF binary.", code=EXIT_TEST_FAILURE)
        log.success(f"OK: {binary.name} is a valid ELF binary")

        # Functional: run under nanvixd on the appropriate platform.
        #
        # On Linux the functional test requires Docker (nanvixd.elf
        # cannot run directly on the CI host).  The ``test`` subcommand
        # does not enable Docker, so functional tests are skipped unless
        # Docker was explicitly configured.
        #
        # On Windows, nanvixd.exe is a native host binary and runs
        # without Docker.
        if sys.platform == "win32":
            self._test_functional_windows(binary)
        elif self.script.docker:
            self._test_functional_docker(binary)
        else:
            log.info("=== skipping functional tests (Docker not configured) ===")

    def _sysroot(self) -> PurePosixPath | Path:
        """Return the sysroot path, translated for Docker if active."""
        sysroot_str = self.script.config.get(CFG_SYSROOT, "")
        if not sysroot_str:
            log.fatal(
                "Sysroot not configured — run 'nanvix-zutil setup' first.",
                code=EXIT_BUILD_FAILURE,
            )
        host = Path(sysroot_str)  # type: ignore[arg-type]
        return self.script.docker.translate_path(host) if self.script.docker else host

    def _test_functional_docker(self, binary: Path) -> None:
        """Run functional tests inside a Docker container (Linux)."""
        log.info("=== bin-hello functional tests (Docker) ===")
        sysroot = self._sysroot()
        workspace_binary = (
            self.script.docker.translate_path(binary) if self.script.docker else binary
        )
        run(
            "timeout",
            "--foreground",
            "60",
            f"{sysroot}/bin/nanvixd.elf",
            "-bin-dir",
            f"{sysroot}/bin",
            "--",
            str(workspace_binary),
            docker=self.script.docker,
        )
        log.success("PASS: bin-hello functional tests")

    def _test_functional_windows(self, binary: Path) -> None:
        """Run functional tests natively on Windows using nanvixd.exe."""
        log.info("=== bin-hello functional tests (Windows) ===")
        sysroot_str = self.script.config.get(CFG_SYSROOT, "")
        if not sysroot_str:
            log.fatal(
                "Sysroot not configured — run 'nanvix-zutil setup' first.",
                code=EXIT_TEST_FAILURE,
            )
        sysroot = Path(sysroot_str)  # type: ignore[arg-type]
        nanvixd = sysroot / "bin" / "nanvixd.exe"
        if not nanvixd.exists():
            log.fatal(
                f"{nanvixd} not found — run 'nanvix-zutil setup' to download it.",
                code=EXIT_TEST_FAILURE,
            )
        run(
            str(nanvixd),
            "-bin-dir",
            str(sysroot / "bin"),
            "--",
            str(binary),
            cwd=repo_root(),
            timeout=60,
        )
        log.success("PASS: bin-hello functional tests")
