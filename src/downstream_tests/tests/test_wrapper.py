"""test_wrapper.py -- Tests for scripts/downstream/wrapper.py."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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
    platform, _, _, remaining = wrapper._parse_wrapper_args(  # type: ignore[attr-defined]
        ["--platform", "windows", "--dry-run"]
    )
    assert platform == "windows"
    assert "--dry-run" in remaining
    # --platform should NOT appear in remaining
    assert "--platform" not in remaining


def test_parse_repos_root_detected(wrapper: types.ModuleType) -> None:
    _, _, has_repos_root, _ = wrapper._parse_wrapper_args(  # type: ignore[attr-defined]
        ["--repos-root", "/tmp/repos"]
    )
    assert has_repos_root is True


def test_parse_repos_root_equals_form(wrapper: types.ModuleType) -> None:
    _, _, has_repos_root, _ = wrapper._parse_wrapper_args(  # type: ignore[attr-defined]
        ["--repos-root=/tmp/repos"]
    )
    assert has_repos_root is True


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

    config_path = Path("/tmp/fake.json")

    with patch.object(wrapper.subprocess, "call", side_effect=fake_call):  # type: ignore[attr-defined]
        with patch.object(wrapper, "_to_windows_path", return_value="C:\\src"):  # type: ignore[attr-defined]
            with patch.object(wrapper, "sys") as mock_sys:  # type: ignore[attr-defined]
                mock_sys.platform = "linux"
                wrapper._run_windows(  # type: ignore[attr-defined]
                    config_path,
                    has_repos_root=False,
                    user_args=["--dry-run"],
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


# ---------------------------------------------------------------------------
# Regression: Windows repos root path separators
# ---------------------------------------------------------------------------


def test_resolve_repos_root_windows_backslashes(
    wrapper: types.ModuleType, tmp_path: Path
) -> None:
    """Windows repos root must use only backslashes, not mixed separators.

    Regression: ``Path(win_userprofile) / "repos"`` on WSL produced
    ``C:\\Users\\user/repos`` because Path is PosixPath on Linux/WSL.
    """
    config_path = tmp_path / "downstream.json"
    # No config file -> will use auto-detect path

    fake_userprofile = "C:\\Users\\testuser"

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        r = MagicMock()
        r.stdout = fake_userprofile + "\n"
        r.returncode = 0
        return r

    with patch.object(wrapper.subprocess, "run", side_effect=fake_run):  # type: ignore[attr-defined]
        result: str = wrapper._resolve_repos_root(config_path, for_windows=True)  # type: ignore[attr-defined]

    # Must be a pure-backslash Windows path
    assert result == "C:\\Users\\testuser\\repos"
    assert "/" not in result, f"Forward slash in Windows path: {result}"


def test_resolve_repos_root_windows_trailing_backslash(
    wrapper: types.ModuleType, tmp_path: Path
) -> None:
    """Trailing backslash on USERPROFILE should not produce double backslash."""
    config_path = tmp_path / "downstream.json"

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        r = MagicMock()
        r.stdout = "C:\\Users\\testuser\\\n"
        r.returncode = 0
        return r

    with patch.object(wrapper.subprocess, "run", side_effect=fake_run):  # type: ignore[attr-defined]
        result: str = wrapper._resolve_repos_root(config_path, for_windows=True)  # type: ignore[attr-defined]

    assert result == "C:\\Users\\testuser\\repos"


def test_resolve_repos_root_linux(wrapper: types.ModuleType, tmp_path: Path) -> None:
    """Linux repos root uses expanduser, no Windows detection."""
    config_path = tmp_path / "downstream.json"

    result: str = wrapper._resolve_repos_root(config_path, for_windows=False)  # type: ignore[attr-defined]
    assert result == str(Path("~/repos").expanduser())
