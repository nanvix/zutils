"""wheel.py -- Wheel build helper for downstream_tests."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .log import dry, fail, log, ok

# Timeout in seconds for wheel build subprocess calls.
_BUILD_TIMEOUT = 300


def build_wheel(
    zutils_root: Path,
    work_dir: Path,
    *,
    skip_build: bool = False,
    dry_run: bool = False,
) -> Path:
    """Build a nanvix-zutil wheel into ``work_dir/wheel/``.

    Tries ``pip``, then ``uv pip``, then ``python -m pip`` in order.

    Args:
        zutils_root: Root of the nanvix-zutil source tree.
        work_dir:    Scratch directory; the wheel is placed in
                     ``work_dir/wheel/``.
        skip_build:  When True, reuse an existing ``.whl`` file.
        dry_run:     When True, return a placeholder path without building.

    Returns:
        Path to the wheel file.

    Raises:
        SystemExit: On failure to build or find a wheel.
    """
    wheel_dir = work_dir / "wheel"

    if dry_run:
        dry(f"would build wheel from {zutils_root} -> {wheel_dir}")
        return wheel_dir / "nanvix_zutil-dry_run-py3-none-any.whl"

    if skip_build:
        existing = list(wheel_dir.glob("*.whl"))
        if existing:
            ok(f"Reusing: {existing[0].name}")
            return existing[0]
        fail(f"No wheel found in {wheel_dir}. Run without --skip-build first.")
        sys.exit(1)

    log(f"Building nanvix-zutil wheel from {zutils_root}")
    wheel_dir.mkdir(parents=True, exist_ok=True)
    for whl in wheel_dir.glob("*.whl"):
        whl.unlink()

    pip_cmd: Optional[list[str]] = None
    if shutil.which("pip"):
        pip_cmd = ["pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(zutils_root)]
    elif shutil.which("uv"):
        pip_cmd = ["uv", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(zutils_root)]
    else:
        pip_cmd = [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(zutils_root),
        ]

    subprocess.run(pip_cmd, check=True, timeout=_BUILD_TIMEOUT)

    wheels = list(wheel_dir.glob("*.whl"))
    if not wheels:
        fail("Wheel build produced no .whl file")
        sys.exit(1)

    ok(f"Built: {wheels[0].name}")
    return wheels[0]
