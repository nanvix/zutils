# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-world example — minimal ZScript consumer.

Demonstrates the lifecycle hooks with a trivial C project:

    ./z setup      # verify that gcc is available
    ./z build      # compile src/main.c → build/hello
    ./z test       # run the binary
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
_VENV_PYTHON = _VENV / ("Scripts" if os.name == "nt" else "bin") / "python"
_ZUTILS_SRC = _NANVIX_DIR.parents[2]  # examples/hello-world/../../ → zutils/

if not sys.prefix.startswith(str(_VENV)):
    if not _VENV.exists():
        print("bootstrap: creating venv …", flush=True)
        subprocess.check_call([sys.executable, "-m", "venv", str(_VENV)])
    print("bootstrap: installing nanvix-zutil from source …", flush=True)
    subprocess.check_call(
        [str(_VENV_PYTHON), "-m", "pip", "install", "-q", str(_ZUTILS_SRC)]
    )
    os.execv(
        str(_VENV_PYTHON),
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
    )

# ---------------------------------------------------------------------------
# Consumer build script — everything below is what a real repo writes.
# ---------------------------------------------------------------------------

import shutil  # noqa: E402

from nanvix_zutil import ZScript, log  # noqa: E402


class HelloWorld(ZScript):
    """Build script for the hello-world C example."""

    def setup(self) -> None:
        """Check that a C compiler is available."""
        if shutil.which("gcc") is None:
            log.fatal(
                "gcc not found",
                code=3,
                hint="Install a C compiler (e.g. apt install gcc).",
            )
        log.success("gcc found — ready to build")

    def build(self) -> None:
        """Compile the project via Make."""
        self.run("make", "-f", "Makefile", "all")
        log.success("build complete")

    def test(self) -> None:
        """Run the compiled binary."""
        self.run("./build/hello")
        log.success("test passed")

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run("make", "-f", "Makefile", "clean")
        log.success("clean complete")


if __name__ == "__main__":
    HelloWorld.main()
