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
from pathlib import Path, PurePosixPath

from nanvix_zutil import (
    BUILDROOT_CONTAINER_PATH,
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    DockerConfig,
    ZScript,
    log,
)
from nanvix_zutil.exitcodes import EXIT_BUILD_FAILURE, EXIT_TEST_FAILURE


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
    # Internal helpers
    # ------------------------------------------------------------------

    def _toolchain(self) -> PurePosixPath | Path:
        """Return the toolchain root, translated for Docker if active."""
        host = Path(self.config.get(CFG_TOOLCHAIN, "/opt/nanvix") or "/opt/nanvix")
        return self.translate_path(host) if self.docker else host

    def _sysroot(self) -> PurePosixPath | Path:
        """Return the sysroot path, translated for Docker if active."""
        sysroot_str = self.config.get(CFG_SYSROOT, "")
        if not sysroot_str:
            log.fatal(
                "Sysroot not configured — run 'nanvix-zutil setup' first.",
                code=EXIT_BUILD_FAILURE,
            )
        host = Path(sysroot_str)  # type: ignore[arg-type]
        return self.translate_path(host) if self.docker else host

    def _buildroot_path(self) -> PurePosixPath | Path:
        """Return the effective buildroot path (translated for Docker if active)."""
        if self.docker:
            return BUILDROOT_CONTAINER_PATH
        return self.nanvix_dir / "buildroot"

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
        tc = self._toolchain()
        sysroot = self._sysroot()
        buildroot = self._buildroot_path()
        cc = str(tc / "bin" / "i686-nanvix-gcc")
        cflags = f"-O2 -Wall -msse2 -mfpmath=sse -I{buildroot}/include"
        ldflags = f"-T{sysroot}/lib/user.ld -static -Wl,-z,noexecstack"
        libs = (
            f"-Wl,--start-group"
            f" {buildroot}/lib/libhello.a"
            f" {sysroot}/lib/libposix.a"
            f" {tc}/i686-nanvix/lib/libc.a"
            f" {tc}/i686-nanvix/lib/libm.a"
            f" -Wl,--end-group"
        )

        # Single shell invocation so intermediate .o survives across
        # compile and link steps inside the same Docker container.
        self.run(
            "sh",
            "-c",
            f"{cc} {cflags} -c -o main.o src/main.c"
            f" && {cc} {cflags} {ldflags} -o hello.elf main.o {libs}",
        )

    def test(self) -> None:
        """Run the test suite (smoke + integration + functional).

        The functional test phase runs under ``nanvixd.elf`` inside a
        Docker container on Linux, or natively under ``nanvixd.exe`` on
        Windows.  It is skipped only when Docker is not configured **and**
        the platform is not Windows.
        """
        binary = self.repo_root / "hello.elf"

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
        if sys.platform == "win32":
            self._test_functional_windows(binary)
        else:
            self._test_functional_linux(binary)

    def _test_functional_linux(self, binary: Path) -> None:
        """Run functional tests under nanvixd.elf (native or Docker)."""
        log.info("=== bin-hello functional tests (Linux) ===")
        sysroot = self._sysroot()
        workspace_binary = self.translate_path(binary)
        self.run(
            "timeout",
            "--foreground",
            "60",
            f"{sysroot}/bin/nanvixd.elf",
            "-bin-dir",
            f"{sysroot}/bin",
            "--",
            str(workspace_binary),
        )
        log.success("PASS: bin-hello functional tests")

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
        self.run(
            str(nanvixd),
            "-bin-dir",
            str(sysroot / "bin"),
            "--",
            str(binary),
        )
        log.success("PASS: bin-hello functional tests")

    def clean(self) -> None:
        """Remove build artifacts."""
        for name in ("main.o", "hello.elf"):
            artifact = self.repo_root / name
            if artifact.exists():
                artifact.unlink()


if __name__ == "__main__":
    BinHello.main()
