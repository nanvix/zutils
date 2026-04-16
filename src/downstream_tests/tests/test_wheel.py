"""test_wheel.py — Tests for downstream_tests.wheel."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from downstream_tests.wheel import build_wheel


def _make_run_result(returncode: int = 0):
    r = MagicMock()
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# build_wheel
# ---------------------------------------------------------------------------


def test_build_wheel_with_pip(tmp_path):
    """When pip is available, build_wheel runs pip wheel and returns the .whl path."""
    zutils_root = tmp_path / "zutils"
    zutils_root.mkdir()
    work_dir = tmp_path / "work"
    wheel_dir = work_dir / "wheel"

    def mock_run(cmd, **kwargs):
        # Create a fake wheel file when pip wheel is invoked
        wheel_dir.mkdir(parents=True, exist_ok=True)
        (wheel_dir / "nanvix_zutil-1.0.0-py3-none-any.whl").touch()
        return _make_run_result(0)

    with patch("shutil.which", return_value="/usr/bin/pip"):
        with patch("subprocess.run", side_effect=mock_run):
            result = build_wheel(zutils_root, work_dir)

    assert result.suffix == ".whl"
    assert result.exists()


def test_build_wheel_skip_reuses(tmp_path):
    """With skip_build=True and an existing wheel, reuse it."""
    zutils_root = tmp_path / "zutils"
    work_dir = tmp_path / "work"
    wheel_dir = work_dir / "wheel"
    wheel_dir.mkdir(parents=True)
    existing_whl = wheel_dir / "nanvix_zutil-1.0.0-py3-none-any.whl"
    existing_whl.touch()

    result = build_wheel(zutils_root, work_dir, skip_build=True)

    assert result == existing_whl


def test_build_wheel_skip_no_wheel(tmp_path):
    """With skip_build=True and no wheel file, SystemExit is raised."""
    zutils_root = tmp_path / "zutils"
    work_dir = tmp_path / "work"
    wheel_dir = work_dir / "wheel"
    wheel_dir.mkdir(parents=True)
    # No .whl file present

    with pytest.raises(SystemExit):
        build_wheel(zutils_root, work_dir, skip_build=True)


def test_build_wheel_dry_run(tmp_path):
    """In dry-run mode, returns a placeholder path without building."""
    zutils_root = tmp_path / "zutils"
    work_dir = tmp_path / "work"

    with patch("subprocess.run") as mock_run:
        result = build_wheel(zutils_root, work_dir, dry_run=True)
        mock_run.assert_not_called()

    assert "dry_run" in result.name


def test_build_wheel_fallback_chain(tmp_path):
    """pip not found → tries uv; uv not found → falls back to python -m pip."""
    zutils_root = tmp_path / "zutils"
    zutils_root.mkdir()
    work_dir = tmp_path / "work"
    wheel_dir = work_dir / "wheel"

    def mock_run(cmd, **kwargs):
        wheel_dir.mkdir(parents=True, exist_ok=True)
        (wheel_dir / "nanvix_zutil-1.0.0-py3-none-any.whl").touch()
        return _make_run_result(0)

    # pip=None, uv=None → falls through to sys.executable -m pip
    with patch("shutil.which", return_value=None):
        with patch("subprocess.run", side_effect=mock_run) as mock_sub:
            result = build_wheel(zutils_root, work_dir)

    called_cmd = mock_sub.call_args[0][0]
    assert called_cmd[0] == sys.executable
    assert "-m" in called_cmd
    assert "pip" in called_cmd
    assert result.suffix == ".whl"
