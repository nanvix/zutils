# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""bin-hello example — cross-compiles a binary that depends on lib-hello.

Demonstrates dependency resolution with ``nanvix.toml``.  Run with
``--help`` to see available subcommands and Docker flags::

    nanvix-zutil setup                     # download sysroot + lib-hello (Docker auto-enabled)
    nanvix-zutil setup --with-docker IMG   # download sysroot + lib-hello (custom Docker image)
    nanvix-zutil build                     # cross-compile inside Docker container (auto)
    nanvix-zutil test                      # run tests (smoke + integration + functional)
    nanvix-zutil clean                     # remove build artifacts (host)
"""

import dataclasses
import sys
from pathlib import Path

from nanvix_zutil import (
    BUILDROOT_CONTAINER_PATH,
    CFG_SYSROOT,
    TOOLCHAIN_CONTAINER_PATH,
    DockerConfig,
    ZScript,
    log,
)
from nanvix_zutil.docker import SYSROOT_CONTAINER_PATH
from nanvix_zutil.exitcodes import EXIT_TEST_FAILURE
from nanvix_zutil.helpers import InitRdArgs, make_initrd, run
from nanvix_zutil.paths import repo_root, sysroot


class BinHello(ZScript):
    """Build script for the bin-hello binary example."""

    # ------------------------------------------------------------------
    # Docker configuration
    # ------------------------------------------------------------------

    def docker_config(self, image: str) -> DockerConfig:
        """Add output_files so hello.elf is copied back on Windows."""
        cfg = super().docker_config(image)
        return dataclasses.replace(cfg, output_files=["hello.elf"])

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def setup(self) -> bool:
        """Download the Nanvix sysroot and lib-hello dependency, then verify."""
        used_fallback = super().setup()
        if self.buildroot is None:
            self.log.fatal(
                "nanvix.toml must declare lib-hello as a build-time dependency.",
                hint=(
                    "Add lib-hello as a dependency in nanvix.toml, then "
                    "re-run `nanvix-zutil setup`."
                ),
            )
        self.buildroot.verify(["libhello.a"])
        return used_fallback

    def build(self) -> None:
        """Cross-compile main.c into hello.elf for Nanvix."""
        tc = TOOLCHAIN_CONTAINER_PATH
        _sysroot = SYSROOT_CONTAINER_PATH
        buildroot = BUILDROOT_CONTAINER_PATH
        cc = str(tc / "bin" / "i686-nanvix-gcc")
        cflags = f"-O2 -Wall -msse2 -mfpmath=sse -I{buildroot}/include"
        ldflags = f"-T{_sysroot}/lib/user.ld -static -Wl,-z,noexecstack"
        libs = (
            f"-Wl,--start-group"
            f" {buildroot}/lib/libhello.a"
            f" {_sysroot}/lib/libposix.a"
            f" {tc}/i686-nanvix/lib/libc.a"
            f" {tc}/i686-nanvix/lib/libm.a"
            f" -Wl,--end-group"
        )

        # Single shell invocation so intermediate .o survives across
        # compile and link steps inside the same Docker container.
        run(
            "sh",
            "-c",
            f"{cc} {cflags} -c -o main.o src/main.c"
            f" && {cc} {cflags} {ldflags} -o hello.elf main.o {libs}",
            cwd=repo_root(),
            docker=self.docker,
        )

        # For standalone deployment mode, produce an initrd image
        # containing the system daemons and the application binary.
        if self.config.deployment_mode == "standalone":
            make_initrd(self, "hello.elf", for_release=True, args=InitRdArgs())

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
        elif self.docker:
            self._test_functional_docker(binary)
        else:
            log.info("=== skipping functional tests (Docker not configured) ===")

    # TODO: Tests should NOT run in Docker.
    def _test_functional_docker(self, binary: Path) -> None:
        """Run functional tests inside a Docker container (Linux)."""
        log.info("=== bin-hello functional tests (Docker) ===")
        workspace_binary = self.translate_path(binary)
        _sysroot = self.translate_path(sysroot())
        self.run(
            "timeout",
            "--foreground",
            "60",
            f"{_sysroot}/bin/nanvixd.elf",
            "-bin-dir",
            f"{_sysroot}/bin",
            "--",
            str(workspace_binary),
        )
        log.success("PASS: bin-hello functional tests")

    # TODO: There are currently two paths to resolve sysroot.
    # The first is hardcoded at .nanvix/sysroot. This is the canonical version.
    # The second is via CFG_SYSROOT, for global sysroot installs.
    # Instead of using CFG_SYSROOT, we should place a symlink
    # at .nanvix/sysroot.
    def _test_functional_windows(self, binary: Path) -> None:
        """Run functional tests natively on Windows using nanvixd.exe."""
        log.info("=== bin-hello functional tests (Windows) ===")
        sysroot_str = self.config.get(CFG_SYSROOT, "")
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

    def clean(self) -> None:
        """Remove build artifacts."""
        for name in ("main.o", "hello.elf", "hello.img"):
            artifact = repo_root() / name
            if artifact.exists():
                artifact.unlink()


if __name__ == "__main__":
    BinHello.main()
