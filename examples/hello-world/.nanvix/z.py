# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-world example — cross-compiles a C program for Nanvix.

Demonstrates the full lifecycle with a real Nanvix build.  Run with
``--help`` to see available subcommands and Docker flags::

    nanvix-zutil setup --with-docker       # download sysroot + enable Docker (default image)
    nanvix-zutil setup --with-docker IMG   # download sysroot + enable Docker (custom image)
    nanvix-zutil build                     # cross-compile inside Docker container (auto)
    nanvix-zutil test                      # run tests inside Docker (auto)
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


class HelloWorld(ZScript):
    """Build script for the hello-world C example."""

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
        """Cross-compile hello.c into hello.elf for Nanvix."""
        tc = self._toolchain()
        sysroot = self._sysroot()
        cc = str(tc / "bin" / "i686-nanvix-gcc")
        cflags = ["-O2", "-Wall", "-msse2", "-mfpmath=sse"]
        ldflags = [f"-T{sysroot}/lib/user.ld", "-static", "-Wl,-z,noexecstack"]
        libs = [
            "-Wl,--start-group",
            f"{sysroot}/lib/libposix.a",
            f"{tc}/i686-nanvix/lib/libc.a",
            f"{tc}/i686-nanvix/lib/libm.a",
            "-Wl,--end-group",
        ]

        self.run(cc, *cflags, "-c", "-o", "hello.o", "src/hello.c")
        self.run(cc, *cflags, *ldflags, "-o", "hello.elf", "hello.o", *libs)

    def test(self) -> None:
        """Run the test suite (smoke + integration + functional).

        Note:
            The functional test phase uses ``timeout --foreground`` (GNU
            coreutils) and KVM. This is Linux-only by design — functional
            tests require ``/dev/kvm`` inside a Docker container.
        """
        binary = self.repo_root / "hello.elf"

        # Smoke: binary must exist and be non-trivially sized.
        log.info("=== hello-world smoke tests ===")
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
        log.info("=== hello-world integration tests ===")
        with binary.open("rb") as fh:
            magic = fh.read(4)
        if magic != b"\x7fELF":
            log.fatal(f"{binary} is not a valid ELF binary.", code=EXIT_TEST_FAILURE)
        log.success(f"OK: {binary.name} is a valid ELF binary")

        # Functional: run under nanvixd.elf (requires KVM).
        log.info("=== hello-world functional tests ===")
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
            kvm=True,
        )
        log.success("PASS: hello-world functional tests")

    def clean(self) -> None:
        """Remove build artifacts."""
        for name in ("hello.o", "hello.elf"):
            artifact = self.repo_root / name
            if artifact.exists():
                artifact.unlink()


if __name__ == "__main__":
    HelloWorld.main()
