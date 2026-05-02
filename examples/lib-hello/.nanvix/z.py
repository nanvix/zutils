# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""lib-hello example — cross-compiles a static library for Nanvix.

Demonstrates the full lifecycle with a real Nanvix build.  Run with
``--help`` to see available subcommands and Docker flags::

    nanvix-zutil setup --with-docker       # download sysroot + enable Docker (default image)
    nanvix-zutil setup --with-docker IMG   # download sysroot + enable Docker (custom image)
    nanvix-zutil build                     # cross-compile inside Docker container (auto)
    nanvix-zutil test                      # run tests (verifies libhello.a)
    nanvix-zutil clean                     # remove build artifacts (host)
"""

from pathlib import Path

from nanvix_zutil import (
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    ZScript,
    log,
)
from nanvix_zutil.exitcodes import EXIT_BUILD_FAILURE, EXIT_TEST_FAILURE


class LibHello(ZScript):
    """Build script for the lib-hello static library example."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _toolchain(self) -> Path:
        """Return the toolchain root, translated for Docker if active."""
        host = Path(self.config.get(CFG_TOOLCHAIN, "/opt/nanvix") or "/opt/nanvix")
        return self.translate_path(host) if self.docker else host

    def _sysroot(self) -> Path:
        """Return the sysroot path, translated for Docker if active."""
        sysroot_str = self.config.get(CFG_SYSROOT, "")
        if not sysroot_str:
            log.fatal(
                "Sysroot not configured — run 'nanvix-zutil setup' first.",
                code=EXIT_BUILD_FAILURE,
            )
        host = Path(sysroot_str)  # type: ignore[arg-type]
        return self.translate_path(host) if self.docker else host

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def build(self) -> None:
        """Cross-compile hello.c into libhello.a for Nanvix."""
        tc = self._toolchain()
        cc = str(tc / "bin" / "i686-nanvix-gcc")
        ar = str(tc / "bin" / "i686-nanvix-ar")
        cflags = ["-O2", "-Wall", "-msse2", "-mfpmath=sse"]

        self.run(cc, *cflags, "-c", "-o", "hello.o", "src/hello.c")
        self.run(ar, "rcs", "libhello.a", "hello.o")

    def test(self) -> None:
        """Run the test suite (smoke + integration).

        Smoke: libhello.a must exist and be non-trivially sized.
        Integration: verify archive magic (``!<arch>``).
        """
        archive = self.repo_root / "libhello.a"

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
            artifact = self.repo_root / name
            if artifact.exists():
                artifact.unlink()


if __name__ == "__main__":
    LibHello.main()
