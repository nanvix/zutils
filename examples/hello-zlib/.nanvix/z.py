# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-zlib example — cross-compiles a C program that uses zlib for Nanvix.

Demonstrates dependency downloading with nanvix.toml:

    ./z setup      # download sysroot + zlib
    ./z build      # cross-compile hello-zlib.c → hello-zlib.elf
    ./z test       # run tests (smoke + integration + functional)
    ./z clean      # remove build artifacts
"""

from nanvix_zutil import (
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    ZScript,
)


class HelloZlib(ZScript):
    """Build script for the hello-zlib C example."""

    def _make_args(self, *targets: str) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        toolchain = self.config.get(CFG_TOOLCHAIN, "/opt/nanvix")
        buildroot_path = str(self.nanvix_dir / "buildroot")

        args = [
            "make",
            "-f",
            "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={sysroot}",
            f"NANVIX_TOOLCHAIN={toolchain}",
            f"BUILDROOT={buildroot_path}",
            f"PLATFORM={self.config.machine}",
            f"PROCESS_MODE={self.config.deployment_mode}",
            f"MEMORY_SIZE={self.config.memory_size}",
        ]

        args.extend(targets)
        return args

    def setup(self) -> None:
        """Download the Nanvix sysroot and zlib dependency, then verify."""
        super().setup()
        if self.buildroot is None:
            self.log.fatal(
                "nanvix.toml must declare zlib as a build-time dependency.",
                hint=(
                    "Add zlib as a build-time dependency in nanvix.toml, then "
                    "re-run `./z setup`. See the hello-zlib example manifest "
                    "for reference."
                ),
            )
        self.buildroot.verify(["libz.a"])

    def build(self) -> None:
        """Cross-compile hello-zlib.c into hello-zlib.elf for Nanvix."""
        self.run(*self._make_args("all"), cwd=self.repo_root)

    def test(self) -> None:
        """Run the test suite (smoke + integration + functional)."""
        self.run(*self._make_args("test"), cwd=self.repo_root)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    HelloZlib.main()
