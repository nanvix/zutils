# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-world example — cross-compiles a C program for Nanvix.

Demonstrates the full lifecycle with a real Nanvix build:

    ./z setup      # download Nanvix sysroot
    ./z build      # cross-compile hello.c → hello.elf
    ./z test       # run tests (smoke + integration + functional)
    ./z clean      # remove build artifacts
"""

from nanvix_zutil import CFG_GH_TOKEN, CFG_SYSROOT, CFG_TAG, CFG_TOOLCHAIN, EXIT_MISSING_DEP, Sysroot, ZScript, log


class HelloWorld(ZScript):
    """Build script for the hello-world C example."""

    NANVIX_TAG = "latest"

    def _make_args(self, *targets: str) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        toolchain = self.config.get(CFG_TOOLCHAIN, "/opt/nanvix")

        args = [
            "make",
            "-f",
            "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={sysroot}",
            f"NANVIX_TOOLCHAIN={toolchain}",
            f"PLATFORM={self.config.machine}",
            f"PROCESS_MODE={self.config.deployment_mode}",
            f"MEMORY_SIZE={self.config.memory_size}",
        ]

        args.extend(targets)
        return args

    def setup(self) -> None:
        """Download the Nanvix sysroot."""
        tag = self.config.get(CFG_TAG, self.NANVIX_TAG)
        if not tag:
            log.fatal(f"{CFG_TAG} is not set.", code=EXIT_MISSING_DEP)

        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag=tag,
            gh_token=self.config.get(CFG_GH_TOKEN),
        )
        sysroot.verify(self.sysroot_required_files())
        self.config.set(CFG_SYSROOT, str(sysroot.path))
        self.config.save()

    def build(self) -> None:
        """Cross-compile hello.c into hello.elf for Nanvix."""
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
    HelloWorld.main()
