"""test_wrapper.py -- Tests for scripts/downstream/wrapper.py."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# The wrapper lives outside the package, so import it by path.
_WRAPPER_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "scripts"
    / "downstream"
    / "wrapper.py"
)


@pytest.fixture()
def wrapper() -> types.ModuleType:
    """Import wrapper.py as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("wrapper", _WRAPPER_PATH)
    assert spec is not None, f"Could not load spec from {_WRAPPER_PATH}"
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None, "spec.loader is None"
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# _parse_wrapper_args
# ---------------------------------------------------------------------------


def test_parse_platform_flag(wrapper: types.ModuleType) -> None:
    platform, remaining = wrapper._parse_wrapper_args(  # type: ignore[attr-defined]
        ["--platform", "windows", "--", "--dry-run"]
    )
    assert platform == "windows"
    assert "--dry-run" in remaining
    assert "--platform" not in remaining


def test_parse_platform_equals_form(wrapper: types.ModuleType) -> None:
    platform, remaining = wrapper._parse_wrapper_args(  # type: ignore[attr-defined]
        ["--platform=linux", "--", "--dry-run"]
    )
    assert platform == "linux"
    assert "--dry-run" in remaining
    assert "--platform" not in remaining
    assert "--platform=linux" not in remaining


def test_parse_double_dash_passthrough(wrapper: types.ModuleType) -> None:
    """Everything after -- is forwarded verbatim."""
    platform, remaining = wrapper._parse_wrapper_args(  # type: ignore[attr-defined]
        ["--platform", "linux", "--", "nanvix/cpython", "--with-docker"]
    )
    assert platform == "linux"
    assert remaining == ["nanvix/cpython", "--with-docker"]


def test_parse_downstream_flags_pass_through(wrapper: types.ModuleType) -> None:
    """--config and --repos-root after -- survive unchanged."""
    platform, remaining = wrapper._parse_wrapper_args(  # type: ignore[attr-defined]
        ["--platform", "linux", "--", "--config", "custom.json", "--repos-root", "/tmp"]
    )
    assert platform == "linux"
    assert remaining == ["--config", "custom.json", "--repos-root", "/tmp"]


def test_parse_unknown_wrapper_flag_errors(wrapper: types.ModuleType) -> None:
    """Unknown flags before -- are rejected by argparse."""
    with pytest.raises(SystemExit) as exc_info:
        wrapper._parse_wrapper_args(["--bogus"])  # type: ignore[attr-defined]
    assert exc_info.value.code != 0


def test_parse_help_before_separator(wrapper: types.ModuleType) -> None:
    """--help (without --) shows wrapper help and exits."""
    with pytest.raises(SystemExit) as exc_info:
        wrapper._parse_wrapper_args(["--help"])  # type: ignore[attr-defined]
    assert exc_info.value.code == 0


def test_parse_empty_args(wrapper: types.ModuleType) -> None:
    """No args returns empty platform and empty passthrough."""
    platform, remaining = wrapper._parse_wrapper_args([])  # type: ignore[attr-defined]
    assert platform == ""
    assert remaining == []


def test_parse_only_separator(wrapper: types.ModuleType) -> None:
    """Just -- returns empty platform and empty passthrough."""
    platform, remaining = wrapper._parse_wrapper_args(["--"])  # type: ignore[attr-defined]
    assert platform == ""
    assert remaining == []


# ---------------------------------------------------------------------------
# Regression: PowerShell arg formatting (no commas)
# ---------------------------------------------------------------------------


def test_run_windows_no_comma_in_ps_args(wrapper: types.ModuleType) -> None:
    """PowerShell args must be space-separated, not comma-separated.

    Regression: ``", ".join(...)`` produced PowerShell array literals,
    causing args to be treated as consumer names on the Windows side.
    """
    captured_cmds: list[list[str]] = []

    def fake_call(cmd: list[str], **kwargs: Any) -> int:
        captured_cmds.append(cmd)
        return 0

    with patch.object(wrapper.subprocess, "call", side_effect=fake_call):  # type: ignore[attr-defined]
        with patch.object(wrapper, "_to_windows_path", return_value="C:\\src"):  # type: ignore[attr-defined]
            with patch.object(wrapper, "sys") as mock_sys:  # type: ignore[attr-defined]
                mock_sys.platform = "linux"
                wrapper._run_windows(  # type: ignore[attr-defined]
                    ["--dry-run"],
                )

    assert len(captured_cmds) == 1
    ps_cmd_arg = captured_cmds[0]
    # The pwsh.exe -Command string is the last element
    cmd_str = ps_cmd_arg[-1]
    # Must NOT contain ", " between arguments (comma-separated = PowerShell array)
    assert (
        "', '" not in cmd_str
    ), f"PowerShell command uses comma-separated args (array literal): {cmd_str}"
    # Must contain space-separated single-quoted args
    assert "' '" in cmd_str or "--dry-run" in cmd_str
