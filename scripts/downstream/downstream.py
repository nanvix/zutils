"""Downstream consumer test runner — platform-aware wrapper.

Auto-detects platform (Windows / WSL / Linux) and dispatches to
the appropriate shell script with translated arguments.

Can be invoked directly:
    python scripts/downstream/downstream.py [args...]

Or via tasks.py:
    uv run tasks.py downstream [args...]
"""

import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent


def _is_wsl() -> bool:
    """Detect if running inside Windows Subsystem for Linux."""
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _detect_platform() -> str:
    """Auto-detect the downstream test platform.

    Returns:
        "windows" on native Windows, "both" on WSL, "linux" otherwise.
    """
    if sys.platform == "win32":
        return "windows"
    if _is_wsl():
        return "both"
    return "linux"


def _translate_args_for_ps1(args: list[str]) -> list[str]:
    """Translate unified CLI args to PowerShell parameter names.

    The bash script uses ``--kebab-case`` flags while the PowerShell
    script uses ``-PascalCase`` parameters.  This function bridges the
    two so callers always use the same CLI surface regardless of OS.
    """
    translated: list[str] = []
    consumers: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--platform":
            i += 2  # consumed by caller, not forwarded to PS1
        elif arg == "--setup-only":
            translated.append("-SetupOnly")
            i += 1
        elif arg == "--skip-build":
            translated.append("-SkipBuild")
            i += 1
        elif arg == "--force-fallback":
            translated.append("-ForceFallback")
            i += 1
        elif arg == "--dry-run":
            translated.append("-DryRun")
            i += 1
        elif arg == "--config":
            translated.extend(["-ConfigFile", args[i + 1]])
            i += 2
        elif arg == "--with-docker":
            i += 1  # PS1 always uses docker — ignored
        elif arg.startswith("--"):
            translated.append(arg)
            i += 1
        else:
            consumers.append(arg)
            i += 1
    if consumers:
        translated.extend(["-Consumers", ",".join(consumers)])
    return translated


def main() -> int:
    """Entry point — detect platform, dispatch to the right script."""
    args = list(sys.argv[1:])

    # Auto-detect platform if not specified.
    if "--platform" not in args:
        platform = _detect_platform()
        args = ["--platform", platform, *args]
    else:
        idx = args.index("--platform")
        platform = args[idx + 1] if idx + 1 < len(args) else "linux"

    # On native Windows, invoke the PowerShell script directly.
    if sys.platform == "win32":
        ps1 = _SCRIPT_DIR / "test-downstream.ps1"
        if not ps1.exists():
            print(f"error: {ps1} not found")
            return 1
        translated = _translate_args_for_ps1(args)
        return subprocess.call(
            [
                "pwsh",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1),
                *translated,
            ]
        )

    # On Linux / WSL, invoke the bash script.
    sh = _SCRIPT_DIR / "test-downstream.sh"
    if not sh.exists():
        print(f"error: {sh} not found")
        return 1
    return subprocess.call(["bash", str(sh), *args])


if __name__ == "__main__":
    sys.exit(main())
