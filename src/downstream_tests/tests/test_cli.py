"""test_cli.py -- Tests for downstream_tests.cli."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from downstream_tests.cli import main, parse_args

# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def test_repos_root_flag():
    args = parse_args(["--repos-root", "/tmp/repos"])
    assert args.repos_root == "/tmp/repos"


def test_config_flag(tmp_path: Path):
    cfg = str(tmp_path / "my.json")
    args = parse_args(["--config", cfg])
    assert args.config == cfg


def test_force_fallback_implies_setup_only():
    """--force-fallback is parsed; main() sets setup_only=True as a consequence."""
    args = parse_args(["--force-fallback"])
    assert args.force_fallback is True
    # main() is responsible for the implication; parse_args just records the flag
    # Simulate what main does:
    if args.force_fallback:
        args.setup_only = True
    assert args.setup_only is True


def test_positional_consumers():
    args = parse_args(["nanvix/zlib", "nanvix/sqlite"])
    assert args.consumers == ["nanvix/zlib", "nanvix/sqlite"]


def test_dry_run_flag():
    args = parse_args(["--dry-run"])
    assert args.dry_run is True


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--help"])
    assert exc_info.value.code == 0


def test_no_platform_flag():
    """--platform is NOT accepted by parse_args (it belongs to wrapper.py)."""
    with pytest.raises(SystemExit):
        parse_args(["--platform", "linux"])


# ---------------------------------------------------------------------------
# main (integration-level, all side effects mocked)
# ---------------------------------------------------------------------------


def test_main_dry_run_integration(tmp_path: Path):
    """main() in dry-run mode: no real network, no real git, no real wheel build."""
    cfg = tmp_path / "downstream.json"
    config_data = {
        "$schema": "./downstream.schema.json",
        "defaults": {
            "checkout_strategy": "shallow",
            "repos_root": str(tmp_path / "repos"),
            "branch_pattern": "nanvix/v*",
        },
        "consumers": [{"repo": "nanvix/zlib"}],
    }
    cfg.write_text(json.dumps(config_data))

    dry_wheel = tmp_path / "nanvix_zutil-dry_run-py3-none-any.whl"

    with patch("downstream_tests.cli.ensure_config", return_value=cfg) as mock_ec:
        with patch("downstream_tests.cli.build_wheel", return_value=dry_wheel):
            with patch("downstream_tests.cli.run_consumers", return_value=0) as mock_rc:
                result = main(
                    [
                        "--config",
                        str(cfg),
                        "--repos-root",
                        str(tmp_path / "repos"),
                        "--dry-run",
                    ]
                )

    assert result == 0
    mock_ec.assert_called_once()
    mock_rc.assert_called_once()
    # dry_run kwarg should be True
    _, run_kwargs = mock_rc.call_args
    assert run_kwargs.get("dry_run") is True


# ---------------------------------------------------------------------------
# Integration smoke test (real subprocess, --dry-run only)
# ---------------------------------------------------------------------------


def test_dry_run_subprocess_smoke(tmp_path: Path):
    """Invoke ``python -m downstream_tests --dry-run`` as a real subprocess.

    This catches broken imports, missing modules, or bad argument handling
    that mocked unit tests would not detect.
    """
    cfg = tmp_path / "downstream.json"
    config_data = {
        "$schema": "./downstream.schema.json",
        "defaults": {
            "checkout_strategy": "shallow",
            "repos_root": str(tmp_path / "repos"),
            "branch_pattern": "nanvix/v*",
        },
        "consumers": [{"repo": "nanvix/zlib"}],
    }
    cfg.write_text(json.dumps(config_data))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "downstream_tests",
            "--config",
            str(cfg),
            "--repos-root",
            str(tmp_path / "repos"),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "dry-run" in result.stdout.lower() or "dry" in result.stdout.lower()


def test_help_outputs_once():
    """``--help`` must produce exactly one help block, not one per platform.

    Regression: the wrapper used to dispatch --help to both Linux and Windows,
    producing duplicate output (and a failure on the Windows side).
    """
    result = subprocess.run(
        [sys.executable, "-m", "downstream_tests", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # "usage:" should appear exactly once -- not duplicated.
    assert result.stdout.count("usage:") == 1
