"""test_runner.py -- Tests for downstream_tests.runner and downstream_tests.fallback."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from downstream_tests.fallback import export_fallback_env
from downstream_tests.runner import run_consumer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _make_venv(tmp_path: Path) -> Path:
    """Create a fake venv python binary so run_consumer can locate it."""
    venv_dir = tmp_path / ".nanvix" / "venv"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    python.touch()
    return tmp_path


# ---------------------------------------------------------------------------
# run_consumer
# ---------------------------------------------------------------------------


def test_run_consumer_setup_only(tmp_path: Path):
    """With setup_only=True only the venv+setup subprocess calls are made."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    # venv create, pip install, import check, setup
    side_effects = [
        _run_result(0),  # venv create
        _run_result(0),  # pip install
        _run_result(0, stdout="OK"),  # import check
        _run_result(0, stdout="setup ok"),  # setup
    ]

    with patch("subprocess.run", side_effect=side_effects):
        with patch("shutil.rmtree"):
            _, status = run_consumer(
                "nanvix/zlib",
                repo_dir,
                wheel,
                setup_only=True,
                force_fallback=False,
                with_docker=False,
                dry_run=False,
            )

    assert status == "OK (setup)"


def test_run_consumer_full_phases(tmp_path: Path):
    """With setup_only=False, all three phases are executed."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    side_effects = [
        _run_result(0),  # venv create
        _run_result(0),  # pip install
        _run_result(0, stdout="OK"),  # import check
        _run_result(0),  # setup
        _run_result(0),  # build
        _run_result(0),  # test
    ]

    with patch("subprocess.run", side_effect=side_effects):
        with patch("shutil.rmtree"):
            with patch("shutil.which", return_value="/usr/bin/docker"):
                _, status = run_consumer(
                    "nanvix/zlib",
                    repo_dir,
                    wheel,
                    setup_only=False,
                    force_fallback=False,
                    with_docker=False,
                    dry_run=False,
                )

    assert status == "OK (setup,build,test)"


def test_run_consumer_setup_fails(tmp_path: Path):
    """Non-zero setup returncode -> FAIL (setup)."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    side_effects = [
        _run_result(0),  # venv create
        _run_result(0),  # pip install
        _run_result(0, stdout="OK"),  # import check
        _run_result(1),  # setup fails
    ]

    with patch("subprocess.run", side_effect=side_effects):
        with patch("shutil.rmtree"):
            _, status = run_consumer(
                "nanvix/zlib",
                repo_dir,
                wheel,
                setup_only=False,
                force_fallback=False,
                with_docker=False,
                dry_run=False,
            )

    assert "FAIL" in status and "setup" in status


def test_run_consumer_force_fallback_exit7_no_log(tmp_path: Path):
    """force_fallback + exit code 7 without fallback log -> FAIL."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    side_effects = [
        _run_result(0),  # venv create
        _run_result(0),  # pip install
        _run_result(0, stdout="OK"),  # import check
        _run_result(7),  # setup exits 7, no fallback log
    ]

    with patch("subprocess.run", side_effect=side_effects):
        with patch("shutil.rmtree"):
            with patch("downstream_tests.runner.export_fallback_env"):
                _, status = run_consumer(
                    "nanvix/zlib",
                    repo_dir,
                    wheel,
                    setup_only=True,
                    force_fallback=True,
                    with_docker=False,
                    dry_run=False,
                )

    assert "FAIL" in status


def test_run_consumer_force_fallback_log_match(tmp_path: Path):
    """force_fallback + exit 0 but 'fallback for' in output -> OK (fallback verified)."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    side_effects = [
        _run_result(0),  # venv create
        _run_result(0),  # pip install
        _run_result(0, stdout="OK"),  # import check
        _run_result(0, stdout="fallback for nanvix/zlib"),  # setup
    ]

    with patch("subprocess.run", side_effect=side_effects):
        with patch("shutil.rmtree"):
            with patch("downstream_tests.runner.export_fallback_env"):
                _, status = run_consumer(
                    "nanvix/zlib",
                    repo_dir,
                    wheel,
                    setup_only=True,
                    force_fallback=True,
                    with_docker=False,
                    dry_run=False,
                )

    assert status == "OK (fallback verified)"


def test_run_consumer_force_fallback_not_triggered(tmp_path: Path):
    """force_fallback + exit 0 with no fallback log -> FAIL."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    side_effects = [
        _run_result(0),  # venv create
        _run_result(0),  # pip install
        _run_result(0, stdout="OK"),  # import check
        _run_result(0, stdout="setup done, no fallback here"),  # setup
    ]

    with patch("subprocess.run", side_effect=side_effects):
        with patch("shutil.rmtree"):
            with patch("downstream_tests.runner.export_fallback_env"):
                _, status = run_consumer(
                    "nanvix/zlib",
                    repo_dir,
                    wheel,
                    setup_only=True,
                    force_fallback=True,
                    with_docker=False,
                    dry_run=False,
                )

    assert "FAIL" in status and "fallback" in status.lower()


def test_run_consumer_dry_run(tmp_path: Path):
    """dry_run=True: no subprocess calls, returns OK (dry-run)."""
    repo_dir = tmp_path
    wheel = tmp_path / "wheel.whl"

    with patch("subprocess.run") as mock_run:
        _, status = run_consumer(
            "nanvix/zlib",
            repo_dir,
            wheel,
            setup_only=False,
            force_fallback=False,
            with_docker=False,
            dry_run=True,
        )
        mock_run.assert_not_called()

    assert status == "OK (dry-run)"


def test_run_consumer_with_docker(tmp_path: Path):
    """with_docker=True: --with-docker flag forwarded to build/test commands."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    captured_cmds: list[list[str]] = []

    def capture_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured_cmds.append(list(cmd))
        return _run_result(0)

    with patch("subprocess.run", side_effect=capture_run):
        with patch("shutil.rmtree"):
            with patch("shutil.which", return_value="/usr/bin/docker"):
                _, _status = run_consumer(
                    "nanvix/zlib",
                    repo_dir,
                    wheel,
                    setup_only=False,
                    force_fallback=False,
                    with_docker=True,
                    dry_run=False,
                )

    # Find the build and test commands (they should include --with-docker)
    cmds_with_docker = [c for c in captured_cmds if "--with-docker" in c]
    assert len(cmds_with_docker) >= 2


def test_run_consumer_flags_appended(tmp_path: Path):
    """Per-consumer flags are correctly placed in subprocess commands."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    captured_cmds: list[list[str]] = []

    def capture_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured_cmds.append(list(cmd))
        return _run_result(0)

    flags = {
        "global": ["--verbose"],
        "setup": ["--no-cache"],
        "build": ["--jobs=4"],
        "test": ["--timeout=300"],
    }

    with patch("subprocess.run", side_effect=capture_run):
        with patch("shutil.rmtree"):
            with patch("shutil.which", return_value="/usr/bin/docker"):
                _, status = run_consumer(
                    "nanvix/zlib",
                    repo_dir,
                    wheel,
                    setup_only=False,
                    force_fallback=False,
                    with_docker=False,
                    dry_run=False,
                    flags=flags,
                )

    assert status == "OK (setup,build,test)"

    # Find the setup/build/test commands (skip venv create, pip install, import check)
    phase_cmds = captured_cmds[3:]  # setup, build, test
    assert len(phase_cmds) == 3

    setup_cmd = phase_cmds[0]
    build_cmd = phase_cmds[1]
    test_cmd = phase_cmds[2]

    # Global flag before command name, per-command flag after
    setup_idx = setup_cmd.index("setup")
    assert "--verbose" in setup_cmd[:setup_idx]
    assert "--no-cache" in setup_cmd[setup_idx + 1 :]

    build_idx = build_cmd.index("build")
    assert "--verbose" in build_cmd[:build_idx]
    assert "--jobs=4" in build_cmd[build_idx + 1 :]

    test_idx = test_cmd.index("test")
    assert "--verbose" in test_cmd[:test_idx]
    assert "--timeout=300" in test_cmd[test_idx + 1 :]


def test_run_consumer_no_flags_unchanged(tmp_path: Path):
    """Without flags, commands are unchanged from baseline behavior."""
    repo_dir = _make_venv(tmp_path)
    wheel = tmp_path / "wheel.whl"
    wheel.touch()

    captured_cmds: list[list[str]] = []

    def capture_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured_cmds.append(list(cmd))
        return _run_result(0)

    with patch("subprocess.run", side_effect=capture_run):
        with patch("shutil.rmtree"):
            _, status = run_consumer(
                "nanvix/zlib",
                repo_dir,
                wheel,
                setup_only=True,
                force_fallback=False,
                with_docker=False,
                dry_run=False,
            )

    assert status == "OK (setup)"
    setup_cmd = captured_cmds[3]
    # Command should end with just "setup", no extra flags
    assert setup_cmd[-1] == "setup"


def test_run_consumer_flags_dry_run(tmp_path: Path):
    """Dry-run output reflects flags."""
    repo_dir = tmp_path
    wheel = tmp_path / "wheel.whl"

    flags = {"global": ["--verbose"], "setup": ["--no-cache"]}

    with patch("subprocess.run") as mock_run:
        _, status = run_consumer(
            "nanvix/zlib",
            repo_dir,
            wheel,
            setup_only=True,
            force_fallback=False,
            with_docker=False,
            dry_run=True,
            flags=flags,
        )
        mock_run.assert_not_called()

    assert status == "OK (dry-run)"


# ---------------------------------------------------------------------------
# export_fallback_env
# ---------------------------------------------------------------------------


def test_export_fallback_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Parses nanvix.toml [dependencies] and sets NANVIX_VERSION_* env vars."""
    nanvix_dir = tmp_path / ".nanvix"
    nanvix_dir.mkdir()
    toml = nanvix_dir / "nanvix.toml"
    toml.write_text(
        '[project]\nname = "myrepo"\n\n[dependencies]\nbuildroot = "2024.02.01"\nmusl = "1.2.5"\n'
    )

    monkeypatch.delenv("NANVIX_VERSION_BUILDROOT", raising=False)
    monkeypatch.delenv("NANVIX_VERSION_MUSL", raising=False)

    export_fallback_env(tmp_path)

    assert os.environ["NANVIX_VERSION_BUILDROOT"] == "2024.02.01-nanvix-99.99.99"
    assert os.environ["NANVIX_VERSION_MUSL"] == "1.2.5-nanvix-99.99.99"


def test_export_fallback_env_no_manifest(tmp_path: Path):
    """Raises RuntimeError when nanvix.toml does not exist."""
    with pytest.raises(RuntimeError, match="nanvix.toml"):
        export_fallback_env(tmp_path)
