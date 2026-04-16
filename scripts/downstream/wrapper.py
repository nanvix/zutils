"""Downstream consumer test runner — platform-aware dispatcher.

Detects the current platform and dispatches ``python -m downstream_tests``
with the correct ``--repos-root`` for each requested platform.

Platform capabilities:
    Native Linux          → can test linux only
    WSL                   → can test linux + windows (default: both)
    Native Windows + WSL  → can test linux + windows (default: both)
    Native Windows        → can test windows only

Can be invoked directly:
    python scripts/downstream/wrapper.py [--platform linux|windows|both] [args...]

Or via tasks.py:
    uv run tasks.py downstream [--platform linux] [args...]
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent


def _to_windows_path(posix_path: str) -> str:
    """Convert a POSIX/WSL path to a Windows path via wslpath."""
    r = subprocess.run(
        ["wslpath", "-w", posix_path],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() or posix_path


def _to_posix_path(win_path: str) -> str:
    """Convert a Windows path to a POSIX/WSL path via wslpath."""
    r = subprocess.run(
        ["wslpath", "-u", win_path],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() or win_path


def _is_wsl() -> bool:
    """Detect if running inside Windows Subsystem for Linux."""
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _can_linux() -> bool:
    """Return True if we can run Linux downstream tests."""
    if sys.platform != "win32":
        return True
    # Native Windows — can do Linux if WSL is installed.
    return shutil.which("wsl") is not None


def _can_windows() -> bool:
    """Return True if we can run Windows downstream tests."""
    if sys.platform == "win32":
        return True
    # Linux — can do Windows only from WSL with pwsh.exe available.
    return _is_wsl() and shutil.which("pwsh.exe") is not None


def _default_platform() -> str:
    """Return the default platform based on capabilities."""
    linux = _can_linux()
    windows = _can_windows()
    if linux and windows:
        return "both"
    if windows:
        return "windows"
    return "linux"


def _resolve_repos_root(config_path: Path, *, for_windows: bool) -> str:
    """Read the repos root from downstream.json for the target platform.

    For Windows, uses ``win_repos_root`` from the config (auto-detecting
    via ``cmd.exe`` if not set).  For Linux, uses ``repos_root``.

    Args:
        config_path: Path to downstream.json.
        for_windows: Whether to resolve the Windows repos root.

    Returns:
        Resolved repos root path as a string.
    """
    repos_root = "~/repos"
    win_repos_root = ""

    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            defaults = raw.get("defaults", {})
            repos_root = defaults.get("repos_root", "~/repos")
            win_repos_root = defaults.get("win_repos_root") or ""
        except Exception as exc:
            print(f"warning: failed to read config: {exc}", file=sys.stderr)

    if not for_windows:
        return str(Path(repos_root).expanduser())

    if win_repos_root:
        return win_repos_root

    # Auto-detect Windows repos root via cmd.exe.
    try:
        wp = subprocess.run(
            ["cmd.exe", "/C", "echo %USERPROFILE%"],
            capture_output=True,
            text=True,
        )
        win_userprofile = wp.stdout.strip()
        if win_userprofile:
            # Keep as Windows-native path — this will be passed to
            # Python running on the Windows side via pwsh.exe.
            return str(Path(win_userprofile) / "repos")
    except Exception as exc:
        print(f"warning: failed to detect Windows repos root: {exc}", file=sys.stderr)

    return "~/repos"


def _parse_wrapper_args(
    argv: list[str],
) -> tuple[str, Path, bool, list[str]]:
    """Extract wrapper-specific flags from argv.

    Consumes ``--platform`` (wrapper-only) and peeks at ``--config`` and
    ``--repos-root`` without consuming them.

    Returns:
        (platform, config_path, has_repos_root, remaining_args)
    """
    platform = ""
    config_path = _SCRIPT_DIR / "downstream.json"
    has_repos_root = False
    remaining: list[str] = []

    i = 0
    while i < len(argv):
        if argv[i] == "--platform" and i + 1 < len(argv):
            platform = argv[i + 1]
            i += 2
        else:
            if argv[i] == "--config" and i + 1 < len(argv):
                config_path = Path(argv[i + 1])
            if argv[i] == "--repos-root" or argv[i].startswith("--repos-root="):
                has_repos_root = True
            remaining.append(argv[i])
            i += 1

    return platform, config_path, has_repos_root, remaining


def _run_linux(
    config_path: Path,
    has_repos_root: bool,
    user_args: list[str],
) -> int:
    """Run downstream_tests for Linux."""
    extra: list[str] = []
    if not has_repos_root:
        extra = [
            "--repos-root",
            _resolve_repos_root(config_path, for_windows=False),
        ]
    return subprocess.call(
        [sys.executable, "-m", "downstream_tests", *extra, *user_args]
    )


def _run_windows(
    config_path: Path,
    has_repos_root: bool,
    user_args: list[str],
) -> int:
    """Run downstream_tests for Windows.

    From WSL: dispatches via ``pwsh.exe``.
    From native Windows: runs directly.
    From native Windows with WSL requesting Linux: not called (see _run_linux).
    """
    extra: list[str] = []
    if not has_repos_root:
        extra = [
            "--repos-root",
            _resolve_repos_root(config_path, for_windows=True),
        ]

    if sys.platform == "win32":
        # Native Windows — run directly.
        return subprocess.call(
            [sys.executable, "-m", "downstream_tests", *extra, *user_args]
        )

    # WSL — shell out to pwsh.exe with list-based args (no string interpolation).
    return subprocess.call(
        [
            "pwsh.exe",
            "-NoProfile",
            "-Command",
            "python",
            "-m",
            "downstream_tests",
            *extra,
            *user_args,
        ]
    )


def _run_linux_from_windows(
    config_path: Path,
    has_repos_root: bool,
    user_args: list[str],
) -> int:
    """Run downstream_tests for Linux from native Windows via wsl.exe."""
    extra: list[str] = []
    if not has_repos_root:
        extra = [
            "--repos-root",
            _resolve_repos_root(config_path, for_windows=False),
        ]
    return subprocess.call(
        [
            "wsl",
            "python3",
            "-m",
            "downstream_tests",
            *extra,
            *user_args,
        ]
    )


def main() -> int:
    """Entry point — parse platform, validate capabilities, dispatch."""
    platform, config_path, has_repos_root, user_args = _parse_wrapper_args(
        list(sys.argv[1:])
    )

    if not platform:
        platform = _default_platform()

    if platform not in ("linux", "windows", "both"):
        print(f"error: unknown platform '{platform}' (use linux, windows, or both)")
        return 1

    # Validate that we can actually run the requested platform(s).
    run_linux = platform in ("linux", "both")
    run_windows = platform in ("windows", "both")

    if run_linux and not _can_linux():
        print("error: cannot test linux from this environment")
        return 1
    if run_windows and not _can_windows():
        print("error: cannot test windows from this environment (no WSL/pwsh.exe)")
        return 1

    rc = 0

    if run_linux:
        if sys.platform == "win32":
            rc = _run_linux_from_windows(config_path, has_repos_root, user_args)
        else:
            rc = _run_linux(config_path, has_repos_root, user_args)

    if run_windows:
        if run_linux:
            print()
            print("=" * 64)
            print()
        rc_win = _run_windows(config_path, has_repos_root, user_args)
        rc = rc or rc_win

    return rc


if __name__ == "__main__":
    sys.exit(main())
