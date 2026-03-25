# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-world example — cross-compiles a C program for Nanvix.

Demonstrates the full lifecycle with a real Nanvix build.  Docker mode is
supported via the ``--with-docker`` / ``--with-minimal-docker`` /
``--docker-image`` flags; the build script itself never references Docker
directly — it calls :meth:`~nanvix_zutil.ZScript.run` and Docker wrapping
is transparent::

    ./z setup                     # download Nanvix sysroot (host)
    ./z build --with-docker       # cross-compile inside Docker container
    ./z test  --with-docker       # run tests (smoke + integration + functional)
    ./z clean                     # remove build artifacts (host)
"""

from pathlib import Path

from nanvix_zutil import (
    CFG_GH_TOKEN,
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    Sysroot,
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
            log.fatal("Sysroot not configured — run './z setup' first.", code=EXIT_BUILD_FAILURE)
        host = Path(sysroot_str)  # type: ignore[arg-type]
        return self.translate_path(host) if self.docker else host

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Download the Nanvix sysroot."""
        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag=self.manifest.sysroot_ref.value,
            gh_token=self.config.get(CFG_GH_TOKEN),
        )
        sysroot.verify(self.sysroot_required_files())
        self.config.set(CFG_SYSROOT, str(sysroot.path))
        self.config.save()

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
        """Run the test suite (smoke + integration + functional)."""
        binary = self.repo_root / "hello.elf"

        # Smoke: binary must exist and be non-trivially sized.
        log.info("=== hello-world smoke tests ===")
        if not binary.exists():
            log.fatal(f"{binary} not found — run './z build' first.", code=EXIT_TEST_FAILURE)
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
            "--",
            str(workspace_binary),
            kvm=True,
        )
        log.success("PASS: hello-world functional tests")

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run("rm", "-f", "hello.o", "hello.elf", docker=False)


if __name__ == "__main__":
    HelloWorld.main()

