# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""lib-hello example — cross-compiles a static library for Nanvix.

Demonstrates the full lifecycle with a real Nanvix build.  Run with
``--help`` to see available subcommands and Docker flags::

    nanvix-zutil setup                     # download sysroot (Docker auto-enabled)
    nanvix-zutil setup                     # use immutable manifest SDK image
    nanvix-zutil build                     # cross-compile inside Docker container (auto)
    nanvix-zutil test                      # run tests (verifies libhello.a)
    nanvix-zutil clean                     # remove build artifacts (host)
"""

import dataclasses
import shutil

from nanvix_zutil import (
    TOOLCHAIN_CONTAINER_PATH,
    DockerConfig,
    ZScript,
    log,
)
from nanvix_zutil.exitcodes import EXIT_TEST_FAILURE
from nanvix_zutil.helpers import run
from nanvix_zutil.paths import dev_out, repo_root


class LibHello(ZScript):
    """Build script for the lib-hello static library example."""

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def build(self, docker: DockerConfig) -> None:
        """Cross-compile hello.c into libhello.a for Nanvix."""
        # output_files copies libhello.a back to the workspace on Windows.
        docker = dataclasses.replace(docker, output_files=["libhello.a"])
        tc = TOOLCHAIN_CONTAINER_PATH
        cc = f"{tc}/bin/clang --target=i686-unknown-nanvix --sysroot={tc}"
        ar = str(tc / "bin" / "llvm-ar")
        cflags = "-O2 -Wall -msse2 -mfpmath=sse"

        # Single shell invocation so intermediate .o survives across
        # compile and archive steps inside the same Docker container.
        run(
            "sh",
            "-c",
            f"{cc} {cflags} -c -o hello.o src/hello.c && {ar} rcs libhello.a hello.o",
            cwd=repo_root(),
            docker=docker,
        )
        # Stage artifacts into the dev tree so `release` packs a standard
        # lib/ + include/ layout.
        libout = dev_out() / "lib"
        inclout = dev_out() / "include"
        libout.mkdir(parents=True, exist_ok=True)
        inclout.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo_root() / "libhello.a", libout)
        shutil.copy(repo_root() / "src" / "hello.h", inclout)

    def test(self) -> None:
        """Run the test suite (smoke + integration).

        Smoke: libhello.a must exist and be non-trivially sized.
        Integration: verify archive magic (``!<arch>``).
        """
        archive = repo_root() / "libhello.a"

        # Smoke: archive must exist and be non-trivially sized.
        log.info("=== lib-hello smoke tests ===")
        if not archive.exists():
            log.fatal(
                f"{archive} not found — run 'nanvix-zutil build' first.",
                code=EXIT_TEST_FAILURE,
            )
        size = archive.stat().st_size
        if size < 8:
            log.fatal(f"{archive} too small ({size} bytes).", code=EXIT_TEST_FAILURE)
        log.success(f"OK: {archive.name} ({size} bytes)")

        # Integration: verify ar archive magic.
        log.info("=== lib-hello integration tests ===")
        with archive.open("rb") as fh:
            magic = fh.read(8)
        if not magic.startswith(b"!<arch>\n"):
            log.fatal(f"{archive} is not a valid ar archive.", code=EXIT_TEST_FAILURE)
        log.success(f"OK: {archive.name} is a valid ar archive")

    def clean(self) -> None:
        """Remove build artifacts."""
        for name in ("hello.o", "libhello.a"):
            artifact = repo_root() / name
            if artifact.exists():
                artifact.unlink()


if __name__ == "__main__":
    LibHello.main()
