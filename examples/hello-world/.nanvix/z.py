# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-world example — cross-compiles a C program for Nanvix.

Demonstrates the full lifecycle with a real Nanvix build:

    ./z setup      # download Nanvix sysroot
    ./z build      # cross-compile hello.c → hello.elf
    ./z test       # run tests (smoke + integration + functional)
    ./z clean      # remove build artifacts
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Self-bootstrap: ensure nanvix_zutil is importable.
# Real consumer repos install the pinned PyPI version; here we install from
# the local source tree so the example works out of the box.
# ---------------------------------------------------------------------------

_NANVIX_DIR = Path(__file__).resolve().parent
_VENV = _NANVIX_DIR / "venv"
_VENV_PYTHON = _VENV / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
_ZUTILS_SRC = _NANVIX_DIR.parents[2]  # examples/hello-world/../../ → zutils/


def _inside_venv() -> bool:
    """Return True if already running inside the project venv."""
    return sys.prefix != sys.base_prefix


def _create_venv() -> None:
    """Create the venv and install nanvix-zutil from local source."""
    print("bootstrap: creating venv …", flush=True)
    subprocess.check_call([sys.executable, "-m", "venv", str(_VENV)])
    print("bootstrap: installing nanvix-zutil (editable) …", flush=True)
    subprocess.check_call(
        [str(_VENV_PYTHON), "-m", "pip", "install", "-q", "-e", str(_ZUTILS_SRC)]
    )


if not _inside_venv():
    if not _VENV_PYTHON.exists():
        _create_venv()
    rc = subprocess.call(
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    )
    sys.exit(rc)

# ---------------------------------------------------------------------------
# Consumer build script — everything below is what a real repo writes.
# ---------------------------------------------------------------------------

from nanvix_zutil import Sysroot, ZScript, log  # noqa: E402


class HelloWorld(ZScript):
    """Build script for the hello-world C example."""

    NANVIX_TAG = "latest"

    def _make_args(self, *targets: str) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get("NANVIX_SYSROOT", "")
        toolchain = self.config.get("NANVIX_TOOLCHAIN", "/opt/nanvix")

        args = [
            "make",
            "-f",
            "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={sysroot}",
            f"NANVIX_TOOLCHAIN={toolchain}",
        ]

        args.extend([
            f"PLATFORM={self.config.machine}",
            f"PROCESS_MODE={self.config.deployment_mode}",
            f"MEMORY_SIZE={self.config.memory_size}",
        ])

        args.extend(targets)
        return args

    def setup(self) -> None:
        """Download the Nanvix sysroot."""
        tag = self.config.get("NANVIX_TAG", self.NANVIX_TAG)
        if not tag:
            log.fatal("NANVIX_TAG is not set.", code=3)

        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag=tag,
            gh_token=self.config.get("GH_TOKEN"),
        )
        sysroot.verify(["lib/libposix.a", "lib/user.ld"])
        self.config.set("NANVIX_SYSROOT", str(sysroot.path))
        self.config.save()
        log.success("Setup complete")

    def build(self) -> None:
        """Cross-compile hello.c into hello.elf for Nanvix."""
        self.config.load()
        self.run(*self._make_args("all"), cwd=self.repo_root)
        log.success("Build complete")

    def test(self) -> None:
        """Run the test suite (smoke + integration + functional)."""
        self.config.load()
        self.run(*self._make_args("test"), cwd=self.repo_root)
        log.success("Tests passed")

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )
        log.success("Clean complete")


if __name__ == "__main__":
    HelloWorld.main()

