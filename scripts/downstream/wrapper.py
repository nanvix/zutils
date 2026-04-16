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
import os
import shutil
import subprocess
import sys
import tempfile
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


def _resolve_repos_root(config_path: Path) -> str:
    """Read the raw repos root string from downstream.json.

    Returns the unexpanded value (e.g. ``~/repos``) so each target
    platform can expand ``~`` to its own home directory.

    Args:
        config_path: Path to downstream.json.

    Returns:
        Raw repos root string from config, or ``"~/repos"`` as default.
    """
    repos_root = "~/repos"

    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            defaults = raw.get("defaults", {})
            repos_root = defaults.get("repos_root", "~/repos")
        except Exception as exc:
            print(f"warning: failed to read config: {exc}", file=sys.stderr)

    return repos_root


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
    *,
    warn_file: str = "",
) -> int:
    """Run downstream_tests for Linux."""
    extra: list[str] = []
    if not has_repos_root:
        extra = [
            "--repos-root",
            _resolve_repos_root(config_path),
        ]
    env = _env_with_pythonpath(
        {"DOWNSTREAM_REPORT_FILE": warn_file} if warn_file else None
    )
    return subprocess.call(
        [sys.executable, "-m", "downstream_tests", *extra, *user_args],
        env=env,
    )


def _run_windows(
    config_path: Path,
    has_repos_root: bool,
    user_args: list[str],
    *,
    warn_file: str = "",
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
            _resolve_repos_root(config_path),
        ]

    if sys.platform == "win32":
        # Native Windows -- run directly.
        env = _env_with_pythonpath(
            {"DOWNSTREAM_REPORT_FILE": warn_file} if warn_file else None
        )
        return subprocess.call(
            [sys.executable, "-m", "downstream_tests", *extra, *user_args],
            env=env,
        )

    # WSL -- shell out to pwsh.exe with PYTHONPATH set so Windows Python
    # can find the downstream_tests package in the WSL source tree.
    win_src = _to_windows_path(str(_SRC_DIR))
    all_args = [*extra, *user_args]
    # Build a PowerShell command that sets PYTHONPATH and invokes python.
    # Use PowerShell array syntax to avoid injection via argument values.
    ps_args = " ".join(f"'{a.replace(chr(39), chr(39) * 2)}'" for a in all_args)
    # For the warn file, convert the WSL path to Windows path so the
    # Windows Python child can write to it.
    warn_env = ""
    if warn_file:
        win_warn = _to_windows_path(warn_file)
        warn_env = f"$env:DOWNSTREAM_REPORT_FILE='{win_warn}'; "
    ps_cmd = f"{warn_env}$env:PYTHONPATH='{win_src}'; python -m downstream_tests {ps_args}".rstrip()
    return subprocess.call(["pwsh.exe", "-NoProfile", "-Command", ps_cmd])


def _run_linux_from_windows(
    config_path: Path,
    has_repos_root: bool,
    user_args: list[str],
    *,
    warn_file: str = "",
) -> int:
    """Run downstream_tests for Linux from native Windows via wsl.exe."""
    extra: list[str] = []
    if not has_repos_root:
        extra = [
            "--repos-root",
            _resolve_repos_root(config_path),
        ]

    # Build a bash command string so env vars survive the WSL launch.
    # Use _SRC_DIR relative to repo root -- inside WSL, expanduser()
    # will resolve ~ and Python will find downstream_tests under src/.
    # We need the WSL-side absolute path to src/, so we ask WSL to
    # resolve _REPO_ROOT via wslpath, then append /src.
    wsl_repo_root = subprocess.run(
        ["wsl", "--", "wslpath", "-u", str(_REPO_ROOT)],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not wsl_repo_root:
        # Fallback: manual conversion.
        rr = str(_REPO_ROOT)
        if len(rr) >= 2 and rr[1] == ":":
            drive = rr[0].lower()
            wsl_repo_root = f"/mnt/{drive}{rr[2:].replace(chr(92), '/')}"
        else:
            wsl_repo_root = rr
    wsl_src = f"{wsl_repo_root}/src"

    env_exports = f"export PYTHONPATH='{wsl_src}'"
    if warn_file:
        env_exports += f"; export DOWNSTREAM_REPORT_FILE='{warn_file}'"
    all_args = [*extra, *user_args]
    bash_args = " ".join(f"'{a}'" for a in all_args)
    bash_cmd = f"{env_exports}; python3 -m downstream_tests {bash_args}".rstrip()
    return subprocess.call(["wsl", "--", "bash", "-c", bash_cmd])


def main() -> int:
    """Entry point -- parse platform, validate capabilities, dispatch."""
    platform, config_path, has_repos_root, user_args = _parse_wrapper_args(
        list(sys.argv[1:])
    )

    # Short-circuit: if --help or -h is in args, run once locally and exit.
    # No need to dispatch to both platforms for help text.
    if "-h" in user_args or "--help" in user_args:
        env = _env_with_pythonpath()
        return subprocess.call(
            [sys.executable, "-m", "downstream_tests", "--help"], env=env
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

    results: list[tuple[str, int]] = []
    platform_warnings: dict[str, int] = {}
    platform_errors: dict[str, int] = {}

    if run_linux:
        _print_platform_banner("Linux")
        report_file = _make_warn_file()
        if sys.platform == "win32":
            rc = _run_linux_from_windows(
                config_path, has_repos_root, user_args, warn_file=report_file
            )
        else:
            rc = _run_linux(
                config_path, has_repos_root, user_args, warn_file=report_file
            )
        results.append(("Linux", rc))
        warns, errs = _read_warn_file(report_file)
        platform_warnings["Linux"] = len(warns)
        platform_errors["Linux"] = len(errs)

    if run_windows:
        _print_platform_banner("Windows")
        report_file = _make_warn_file()
        rc_win = _run_windows(
            config_path, has_repos_root, user_args, warn_file=report_file
        )
        results.append(("Windows", rc_win))
        warns, errs = _read_warn_file(report_file)
        platform_warnings["Windows"] = len(warns)
        platform_errors["Windows"] = len(errs)

    # Combined summary when multiple platforms were tested.
    if len(results) > 1:
        _print_combined_summary(results, platform_warnings, platform_errors)

    return max(rc for _, rc in results) if results else 0


def _make_warn_file() -> str:
    """Create a temp file for the child process to write reports to."""
    fd, path = tempfile.mkstemp(prefix="downstream-report-", suffix=".txt")
    os.close(fd)
    return path


def _read_warn_file(path: str) -> tuple[list[str], list[str]]:
    """Read warnings and errors from a report file and clean up.

    Returns:
        (warnings, errors) tuple of string lists.
    """
    warnings: list[str] = []
    errors: list[str] = []
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        if text:
            for line in text.splitlines():
                if line.startswith("WARN: "):
                    warnings.append(line[6:])
                elif line.startswith("ERROR: "):
                    errors.append(line[7:])
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass
    return warnings, errors


def _env_with_pythonpath(
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return an env dict with PYTHONPATH including src/ (and optional extras)."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{_SRC_DIR}{os.pathsep}{existing}" if existing else str(_SRC_DIR)
    )
    if extra:
        env.update(extra)
    return env


def _print_platform_banner(platform_name: str) -> None:
    """Print a prominent platform banner."""
    banner = f"  Platform: {platform_name}  "
    rule = "#" * max(64, len(banner) + 4)
    print()
    print(f"\033[1;36m{rule}\033[0m")
    print(f"\033[1;36m##{banner:^{len(rule) - 4}}##\033[0m")
    print(f"\033[1;36m{rule}\033[0m")
    print(flush=True)


def _print_combined_summary(
    results: list[tuple[str, int]],
    platform_warnings: dict[str, int] | None = None,
    platform_errors: dict[str, int] | None = None,
) -> None:
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
        # Build a suffix with warning/error counts.
        parts: list[str] = []
        if rc != 0:
            parts.append(f"{rc} failure(s)")
        nwarn = (platform_warnings or {}).get(plat, 0)
        nerr = (platform_errors or {}).get(plat, 0)
        if nerr:
            parts.append(f"{nerr} error(s)")
        if nwarn:
            parts.append(f"{nwarn} warning(s)")
        detail = ", ".join(parts) if parts else "passed"
        print(f"  {marker}  {plat}: {detail}")
    print(f"\033[1m{rule}\033[0m")
    if all_ok:
        print(f"\033[1;32m OK\033[0m All platforms passed!")
    else:
        total_failures = sum(rc for _, rc in results)
        print(
            f"\033[1;31mFAIL\033[0m {total_failures} total failure(s) across platforms"
        )
    print()


if __name__ == "__main__":
    sys.exit(main())
