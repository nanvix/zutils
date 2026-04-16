"""Downstream consumer test runner -- platform-aware dispatcher.

Detects the current platform and dispatches ``python -m downstream_tests``
with the correct ``--repos-root`` for each requested platform.

Platform capabilities:
    Native Linux          -> can test linux only
    WSL                   -> can test linux + windows (default: both)
    Native Windows + WSL  -> can test linux + windows (default: both)
    Native Windows        -> can test windows only

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
_REPO_ROOT = _SCRIPT_DIR.parent.parent
_SRC_DIR = _REPO_ROOT / "src"


def _to_windows_path(posix_path: str) -> str:
    """Convert a POSIX/WSL path to a Windows path via wslpath."""
    r = subprocess.run(
        ["wslpath", "-w", posix_path],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() or posix_path


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
    # Native Windows -- can do Linux if WSL is installed.
    return shutil.which("wsl") is not None


def _can_windows() -> bool:
    """Return True if we can run Windows downstream tests."""
    if sys.platform == "win32":
        return True
    # Linux -- can do Windows only from WSL with pwsh.exe available.
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
            # Keep as Windows-native path -- this will be passed to
            # Python running on the Windows side via pwsh.exe.
            # Use string concat (not Path) to preserve backslashes;
            # on WSL, Path is PosixPath and would use forward slashes.
            return win_userprofile.rstrip("\\") + "\\repos"
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
        # Native Windows -- run directly.
        return subprocess.call(
            [sys.executable, "-m", "downstream_tests", *extra, *user_args]
        )

    # WSL -- shell out to pwsh.exe with PYTHONPATH set so Windows Python
    # can find the downstream_tests package in the WSL source tree.
    win_src = _to_windows_path(str(_SRC_DIR))
    all_args = [*extra, *user_args]
    # Build a PowerShell command that sets PYTHONPATH and invokes python.
    # Use PowerShell array syntax to avoid injection via argument values.
    ps_args = " ".join(f"'{a.replace(chr(39), chr(39) * 2)}'" for a in all_args)
    ps_cmd = (
        f"$env:PYTHONPATH='{win_src}'; python -m downstream_tests {ps_args}".rstrip()
    )
    return subprocess.call(["pwsh.exe", "-NoProfile", "-Command", ps_cmd])


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
            "--",
            "env",
            f"PYTHONPATH={_SRC_DIR}",
            "python3",
            "-m",
            "downstream_tests",
            *extra,
            *user_args,
        ]
    )


def main() -> int:
    """Entry point -- parse platform, validate capabilities, dispatch."""
    platform, config_path, has_repos_root, user_args = _parse_wrapper_args(
        list(sys.argv[1:])
    )

    # Short-circuit: if --help or -h is in args, run once locally and exit.
    # No need to dispatch to both platforms for help text.
    if "-h" in user_args or "--help" in user_args:
        return subprocess.call([sys.executable, "-m", "downstream_tests", "--help"])

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

    results: list[tuple[str, int]] = []

    if run_linux:
        _print_platform_banner("Linux")
        if sys.platform == "win32":
            rc = _run_linux_from_windows(config_path, has_repos_root, user_args)
        else:
            rc = _run_linux(config_path, has_repos_root, user_args)
        results.append(("Linux", rc))

    if run_windows:
        _print_platform_banner("Windows")
        rc_win = _run_windows(config_path, has_repos_root, user_args)
        results.append(("Windows", rc_win))

    # Combined summary when multiple platforms were tested.
    if len(results) > 1:
        _print_combined_summary(results)

    return max(rc for _, rc in results) if results else 0


def _print_platform_banner(platform_name: str) -> None:
    """Print a prominent platform banner."""
    banner = f"  Platform: {platform_name}  "
    rule = "#" * max(64, len(banner) + 4)
    print()
    print(f"\033[1;36m{rule}\033[0m")
    print(f"\033[1;36m##{banner:^{len(rule) - 4}}##\033[0m")
    print(f"\033[1;36m{rule}\033[0m")
    print()


def _print_combined_summary(results: list[tuple[str, int]]) -> None:
    """Print a combined summary across all platform runs."""
    print()
    rule = "=" * 64
    print(f"\033[1m{rule}\033[0m")
    print(f"\033[1m  Combined Summary\033[0m")
    print(f"\033[1m{rule}\033[0m")
    all_ok = True
    for plat, rc in results:
        if rc == 0:
            marker = "\033[1;32m OK\033[0m"
        else:
            marker = "\033[1;31mFAIL\033[0m"
            all_ok = False
        print(f"  {marker}  {plat}: {'passed' if rc == 0 else f'{rc} failure(s)'}")
    print(f"\033[1m{rule}\033[0m")
    if all_ok:
        print(f"\033[1;32m OK\033[0m All platforms passed!")
    else:
        total_failures = sum(rc for _, rc in results)
        print(f"\033[1;31mFAIL\033[0m {total_failures} total failure(s) across platforms")
    print()


if __name__ == "__main__":
    sys.exit(main())
