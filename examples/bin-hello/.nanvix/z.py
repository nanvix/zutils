# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""bin-hello example — cross-compiles a binary that depends on lib-hello.

Demonstrates dependency resolution with ``nanvix.toml``.  Run with
``--help`` to see available subcommands and Docker flags::

    nanvix-zutil setup                     # download sysroot + lib-hello (Docker auto-enabled)
    nanvix-zutil setup                     # use immutable manifest SDK image
    nanvix-zutil build                     # cross-compile inside Docker container (auto)
    nanvix-zutil test                      # run tests (smoke + integration + functional)
    nanvix-zutil clean                     # remove build artifacts (host)
"""

import dataclasses

import _test

from nanvix_zutil import (
    SYSROOT_CONTAINER_PATH,
    TOOLCHAIN_CONTAINER_PATH,
    DockerConfig,
    ZScript,
)
from nanvix_zutil.buildroot import Dependency, Ref, RefKind
from nanvix_zutil.helpers import InitRdArgs, make_initrd, run
from nanvix_zutil.paths import regular_out, repo_root


class BinHello(ZScript):
    """Build script for the bin-hello binary example."""

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def setup(self) -> bool:
        """Set up the sysroot, then install lib-hello from its staged dev tree.

        lib-hello is not a published GitHub package, so it is pulled
        directly from a sibling checkout's staged dev tree instead of
        being declared in ``nanvix.toml``.  Build lib-hello first
        (``./z build`` in ../lib-hello) so its ``out/staging/dev`` exists.
        """
        used_fallback = super().setup()
        manifest = repo_root().parent / "lib-hello" / ".nanvix" / "nanvix.toml"
        assert self.sysroot is not None
        self.sysroot.install_local_archive(
            Dependency(
                "lib-hello", "nanvix/lib-hello", Ref(RefKind.LOCAL, str(manifest))
            ),
            manifest,
        )
        self.sysroot.verify(["lib/libhello.a", "include/hello.h"])
        return used_fallback

    def build(self, docker: DockerConfig) -> None:
        """Cross-compile main.c into hello.elf for Nanvix."""
        # output_files copies hello.elf back to the workspace on Windows.
        docker = dataclasses.replace(docker, output_files=["hello.elf"])
        tc = TOOLCHAIN_CONTAINER_PATH
        sysroot = SYSROOT_CONTAINER_PATH
        cc = f"{tc}/bin/clang --target=i686-unknown-nanvix --sysroot={tc}"
        cflags = f"-O2 -Wall -msse2 -mfpmath=sse -I{sysroot}/include"
        libs = f"{sysroot}/lib/libhello.a"

        # Single shell invocation so intermediate .o survives across
        # compile and link steps inside the same Docker container.
        run(
            "sh",
            "-c",
            f"{cc} {cflags} -c -o main.o src/main.c"
            f" && {cc} {cflags} -o hello.elf main.o {libs}",
            cwd=repo_root(),
            docker=docker,
        )

        # For standalone deployment mode, produce an initrd image
        # containing the system daemons and the application binary.
        if self.config.deployment_mode == "standalone":
            make_initrd(
                repo_root() / "hello.elf",
                regular_out(),
                args=InitRdArgs(),
            )

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
