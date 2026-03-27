# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-transitive example — demonstrates transitive dependency resolution.

Shows how ``nanvix-zutil lock`` discovers transitive dependencies by downloading
each dependency's shallow ``nanvix.lock`` release asset.  In this
example, ``libfoo`` depends on ``zlib``, so locking the manifest
automatically pulls in ``zlib`` as a transitive dependency.

    nanvix-zutil lock       # resolve deps (discovers zlib via libfoo's lockfile)
    nanvix-zutil setup      # download sysroot + all resolved deps
    nanvix-zutil build      # cross-compile hello.c → hello-transitive.elf
    nanvix-zutil test       # run tests (smoke + integration + functional)
    nanvix-zutil clean      # remove build artifacts
"""

from nanvix_zutil import (
    CFG_GH_TOKEN,
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    Buildroot,
    Dependency,
    Sysroot,
    ZScript,
    read_lockfile,
)


class HelloTransitive(ZScript):
    """Build script for the hello-transitive C example."""

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
        """Resolve the dependency graph, then download everything.

        1. Run ``lock()`` to resolve all deps (including transitive ones
           discovered from each dependency's ``nanvix.lock`` release asset).
        2. Download the Nanvix sysroot.
        3. Download every resolved dependency from the lockfile.
        """
        # Resolve the full dependency graph and write nanvix.lock.
        self.lock()

        # Read the generated lockfile to get all resolved deps.
        lockfile = read_lockfile(self.nanvix_dir / "nanvix.lock")

        # Download sysroot.
        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag=self.manifest.sysroot_ref.value,
            gh_token=self.config.get(CFG_GH_TOKEN),
        )
        sysroot.verify(self.sysroot_required_files())
        self.config.set(CFG_SYSROOT, str(sysroot.path))

        # Download all resolved dependencies (direct + transitive).
        buildroot = Buildroot.create(self.nanvix_dir / "buildroot")
        for pkg in lockfile.packages:
            if pkg.kind == "dependency":
                dep = Dependency(name=pkg.name, repo=pkg.repo, ref=pkg.ref)
                buildroot.install_dep(
                    dep=dep,
                    machine=self.config.machine,
                    deployment_mode=self.config.deployment_mode,
                    memory_size=self.config.memory_size,
                    gh_token=self.config.get(CFG_GH_TOKEN),
                )

        self.config.save()

    def build(self) -> None:
        """Cross-compile hello.c into hello-transitive.elf for Nanvix."""
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
    HelloTransitive.main()
