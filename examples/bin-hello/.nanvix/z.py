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
from pathlib import Path, PurePosixPath

import _test

from nanvix_zutil import (
    BUILDROOT_CONTAINER_PATH,
    CFG_SYSROOT,
    TOOLCHAIN_CONTAINER_PATH,
    DockerConfig,
    ZScript,
    log,
)
from nanvix_zutil.exitcodes import EXIT_BUILD_FAILURE
from nanvix_zutil.helpers import InitRdArgs, make_initrd, run
from nanvix_zutil.paths import nanvix_root, repo_root


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

    def _sysroot(self) -> PurePosixPath | Path:
        """Return the sysroot path, translated for Docker if active."""
        sysroot_str = self.config.get(CFG_SYSROOT, "")
        if not sysroot_str:
            log.fatal(
                "Sysroot not configured — run 'nanvix-zutil setup' first.",
                code=EXIT_BUILD_FAILURE,
            )
        host = Path(sysroot_str)  # type: ignore[arg-type]
        return self.docker.translate_path(host) if self.docker else host

    def _buildroot_path(self) -> PurePosixPath | Path:
        """Return the effective buildroot path (translated for Docker if active)."""
        if self.docker:
            return BUILDROOT_CONTAINER_PATH
        return nanvix_root() / "buildroot"

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
            make_initrd(self, "hello.elf", test=False, args=InitRdArgs())

    def test(self) -> None:
        _test.Test(self).test()

    def clean(self) -> None:
        """Remove build artifacts."""
        for name in ("main.o", "hello.elf", "hello.img"):
            artifact = repo_root() / name
            if artifact.exists():
                artifact.unlink()


if __name__ == "__main__":
    BinHello.main()
