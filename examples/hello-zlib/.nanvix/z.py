# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-zlib example — cross-compiles a C program that uses zlib for Nanvix.

Demonstrates dependency downloading with nanvix.toml.  Run with ``--help``
to see available subcommands and Docker flags::

    nanvix-zutil setup --with-docker       # download sysroot + zlib + enable Docker (default)
    nanvix-zutil setup --with-docker IMG   # download sysroot + zlib + enable Docker (custom)
    nanvix-zutil build                     # cross-compile inside Docker container (auto)
    nanvix-zutil test                      # run tests inside Docker (auto)
    nanvix-zutil clean                     # remove build artifacts (host)
"""

from pathlib import Path

from nanvix_zutil import (
    BUILDROOT_CONTAINER_PATH,
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    ZScript,
    log,
)
from nanvix_zutil.exitcodes import EXIT_BUILD_FAILURE, EXIT_TEST_FAILURE


class HelloZlib(ZScript):
    """Build script for the hello-zlib C example."""

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

    def _buildroot_path(self) -> Path:
        """Return the effective buildroot path (translated for Docker if active)."""
        if self.docker:
            return BUILDROOT_CONTAINER_PATH
        return self.nanvix_dir / "buildroot"

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Download the Nanvix sysroot and zlib dependency, then verify."""
        super().setup()
        if self.buildroot is None:
            self.log.fatal(
                "nanvix.toml must declare zlib as a build-time dependency.",
                hint=(
                    "Add zlib as a build-time dependency in nanvix.toml, then "
                    "re-run `nanvix-zutil setup`. See the hello-zlib example manifest "
                    "for reference."
                ),
            )
        self.buildroot.verify(["libz.a"])

    def build(self) -> None:
        """Cross-compile hello-zlib.c into hello-zlib.elf for Nanvix."""
        tc = self._toolchain()
        sysroot = self._sysroot()
        buildroot = self._buildroot_path()
        cc = str(tc / "bin" / "i686-nanvix-gcc")
        cflags = ["-O2", "-Wall", "-msse2", "-mfpmath=sse", f"-I{buildroot}/include"]
        ldflags = [f"-T{sysroot}/lib/user.ld", "-static", "-Wl,-z,noexecstack"]
        libs = [
            "-Wl,--start-group",
            f"{buildroot}/lib/libz.a",
            f"{sysroot}/lib/libposix.a",
            f"{tc}/i686-nanvix/lib/libc.a",
            f"{tc}/i686-nanvix/lib/libm.a",
            "-Wl,--end-group",
        ]

        self.run(cc, *cflags, "-c", "-o", "hello-zlib.o", "src/hello-zlib.c")
        self.run(cc, *cflags, *ldflags, "-o", "hello-zlib.elf", "hello-zlib.o", *libs)

    def test(self) -> None:
        """Run the test suite (smoke + integration + functional).

        Note:
            The functional test phase uses ``timeout --foreground`` (GNU
            coreutils) and KVM. This is Linux-only by design — functional
            tests require ``/dev/kvm`` inside a Docker container.
        """
        binary = self.repo_root / "hello-zlib.elf"

        # Smoke: binary must exist and be non-trivially sized.
        log.info("=== hello-zlib smoke tests ===")
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
        log.info("=== hello-zlib integration tests ===")
        with binary.open("rb") as fh:
            magic = fh.read(4)
        if magic != b"\x7fELF":
            log.fatal(f"{binary} is not a valid ELF binary.", code=EXIT_TEST_FAILURE)
        log.success(f"OK: {binary.name} is a valid ELF binary")

        # Functional: run under nanvixd.elf (requires KVM).
        log.info("=== hello-zlib functional tests ===")
        sysroot = self._sysroot()
        workspace_binary = self.translate_path(binary)
        self.run(
            "timeout",
            "--foreground",
            "60",
            f"{sysroot}/bin/nanvixd.elf",
            "--",
            str(workspace_binary),
            kvm=True,
        )
        log.success("PASS: hello-zlib functional tests")

    def clean(self) -> None:
        """Remove build artifacts."""
        for name in ("hello-zlib.o", "hello-zlib.elf"):
            artifact = self.repo_root / name
            if artifact.exists():
                artifact.unlink()


if __name__ == "__main__":
    HelloZlib.main()
