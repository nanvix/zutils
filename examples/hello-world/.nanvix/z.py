# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Hello-world example — minimal ZScript consumer.

Demonstrates the lifecycle hooks with a trivial Python project:

    ./z setup      # verify Python >= 3.12
    ./z build      # copy src/hello.py → build/
    ./z test       # run the built artifact
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
    rc = subprocess.call(
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    )
    sys.exit(rc)

# ---------------------------------------------------------------------------
# Consumer build script — everything below is what a real repo writes.
# ---------------------------------------------------------------------------

import shutil  # noqa: E402

from nanvix_zutil import ZScript, log  # noqa: E402


class HelloWorld(ZScript):
    """Build script for the hello-world Python example."""

    def setup(self) -> None:
        """Verify Python version is sufficient."""
        major, minor = sys.version_info[:2]
        if (major, minor) < (3, 12):
            log.fatal(
                f"Python 3.12+ required (found {major}.{minor})",
                code=3,
                hint="Install Python 3.12+ and ensure it is on your PATH.",
            )
        log.success(f"Python {major}.{minor} — ready to build")

    def build(self) -> None:
        """Copy source to build directory."""
        build_dir = self.repo_root / "build"
        build_dir.mkdir(exist_ok=True)
        shutil.copy2(self.repo_root / "src" / "hello.py", build_dir / "hello.py")
        log.success("build complete")

    def test(self) -> None:
        """Run the built artifact."""
        self.run(sys.executable, str(self.repo_root / "build" / "hello.py"))
        log.success("test passed")

    def clean(self) -> None:
        """Remove build artifacts."""
        build_dir = self.repo_root / "build"
        if build_dir.exists():
            shutil.rmtree(build_dir)
        log.success("clean complete")


if __name__ == "__main__":
    HelloWorld.main()

