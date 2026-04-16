# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""
Development task runner for nanvix-zutil.

Usage: uv run tasks.py <command>

Commands:
  setup         Configure git hooks and sync dev dependencies
  lint          Run all linters (black, shfmt, shellcheck, PSScriptAnalyzer, yamllint)
  format        Fix code formatting with black
  typecheck     Run strict type checking with basedpyright
  test          Run the test suite with pytest
  test-downstream  Run the downstream_tests unit tests
  ci            Run CI locally using gh act (requires Docker + nanvix toolchain image)
  clean         Remove Python bytecode caches and build artifacts
  release       Build distribution artifacts (wheel + sdist) for release
  downstream    Run downstream consumer tests (auto-detects platform)
  version       Bump version across pyproject.toml and templates
  shell-lint    Check shell script formatting and correctness
  shell-format  Auto-fix shell script formatting with shfmt
  yaml-lint     Lint YAML files with yamllint
"""

import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

SOURCES = ["src/", "tests/"]

# Anchor all repo-relative paths to the directory containing this script so
# that commands like `uv run tasks.py version patch` work regardless of the
# caller's working directory.
_REPO_ROOT = Path(__file__).parent


def _run(*args: str) -> int:
    """Run a command, returning its exit code."""
    print(f"> {' '.join(args)}")
    return subprocess.call(args)


def setup() -> int:
    """Configure git hooks and sync dev dependencies."""
    return _run("git", "config", "--local", "core.hooksPath", ".githooks")


def lint() -> int:
    """Run all linters: black, shfmt, shellcheck, PSScriptAnalyzer, yamllint."""
    rc = 0

    # Python formatting (black)
    code = _run(sys.executable, "-m", "black", "--check", *SOURCES)
    if code != 0:
        rc = 1

    # Shell scripts (shfmt + shellcheck + PSScriptAnalyzer)
    code = shell_lint()
    if code != 0:
        rc = 1

    # YAML (yamllint)
    code = yaml_lint()
    if code != 0:
        rc = 1

    return rc


def format_code() -> int:
    """Fix code formatting with black."""
    return _run(sys.executable, "-m", "black", *SOURCES)


def typecheck() -> int:
    """Run strict type checking with basedpyright."""
    return _run(sys.executable, "-m", "basedpyright", *SOURCES)


def test() -> int:
    """Run the test suite with pytest."""
    return _run(sys.executable, "-m", "pytest", "tests/", "-v")


def test_downstream() -> int:
    """Run the downstream_tests unit tests with pytest."""
    return _run(sys.executable, "-m", "pytest", "src/downstream_tests/tests/", "-v")


def clean() -> int:
    """Remove Python bytecode caches and build artifacts."""
    count = 0
    for pattern, is_dir in [("__pycache__", True), ("*.pyc", False)]:
        for p in Path(".").rglob(pattern):
            if is_dir:
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
            count += 1
    for d in [".pytest_cache", "dist", "build"]:
        path = Path(d)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            count += 1
    print(f"Cleaned {count} item(s).")
    return 0


def ci() -> int:
    """Run CI locally using gh act (requires Docker + nanvix toolchain image).

    Runs the specified CI job (or all jobs) locally using nektos/act via the
    gh CLI extension. The nanvix/toolchain:latest-minimal Docker image must
    be available locally.

    Usage:
        uv run tasks.py ci            # run all CI jobs
        uv run tasks.py ci test       # run only the test job
        uv run tasks.py ci lint       # run only the lint job
    """
    if shutil.which("gh") is None:
        print("error: gh CLI not found. Install from https://cli.github.com/")
        return 1

    result = subprocess.run(
        ["gh", "act", "--help"],
        capture_output=True,
    )
    if result.returncode != 0:
        print("error: gh act extension not found.")
        print("  Install with: gh extension install nektos/gh-act")
        return 1

    target = sys.argv[2] if len(sys.argv) > 2 else "all"

    job_map: dict[str, list[str]] = {
        "lint": ["lint-and-typecheck"],
        "test": ["test"],
        "all": ["lint-and-typecheck", "test"],
    }

    jobs = job_map.get(target)
    if jobs is None:
        print(f"error: Unknown CI target: {target}")
        print(f"  Available: {', '.join(job_map)}")
        return 2

    act_flags = ["--container-architecture", "linux/amd64", "--pull=false"]

    for job in jobs:
        print(f"ci: Running job: {job}")
        rc = _run("gh", "act", "-j", job, *act_flags)
        if rc != 0:
            print(f"ci: Job '{job}' failed.")
            return rc

    print("ci: All jobs passed.")
    return 0


def release() -> int:
    """Build distribution artifacts (wheel and sdist) for release.

    Produces a wheel (.whl) and source distribution (.tar.gz) inside
    dist/.  To publish, trigger the 'Release' GitHub Actions workflow
    (workflow_dispatch) which builds, tags, creates a GitHub release,
    and publishes to PyPI via ``uv publish``.
    """
    uv = shutil.which("uv")
    if uv is None:
        print("error: 'uv' not found on PATH. Install it from https://astral.sh/uv")
        return 1
    code = _run(uv, "build")
    if code != 0:
        return code
    dist = Path("dist")
    artifacts = sorted(dist.glob("*"))
    if artifacts:
        print("Built artifacts:")
        for f in artifacts:
            print(f"  {f}")
    return 0


def downstream() -> int:
    """Run downstream consumer tests (auto-detects platform).

    Delegates to scripts/downstream/wrapper.py which auto-detects
    the platform (Windows / WSL / Linux) and dispatches to the
    appropriate shell script with translated arguments.

    Usage:
        uv run tasks.py downstream                     # auto-detect platform
        uv run tasks.py downstream --platform linux    # override platform
        uv run tasks.py downstream --setup-only sqlite # forward flags
    """
    script = _REPO_ROOT / "scripts" / "downstream" / "wrapper.py"
    if not script.exists():
        print(f"error: downstream runner not found at {script}")
        return 1
    return _run(sys.executable, str(script), *sys.argv[2:])


def _find_bash_scripts() -> list[str]:
    """Discover bash scripts to lint: *.sh, extensionless wrappers, git hooks."""
    files: list[str] = []
    # *.sh files in templates/ and examples/ (excluding vendored sysroot and venv)
    files.extend(
        str(p)
        for p in _REPO_ROOT.rglob("*.sh")
        if ".venv" not in p.parts and "venv" not in p.parts and "sysroot" not in p.parts
    )
    # Extensionless wrappers (templates/z, examples/*/z)
    for pattern in ["templates/z", "examples/*/z"]:
        files.extend(str(p) for p in _REPO_ROOT.glob(pattern))
    # Git hooks
    hooks_dir = _REPO_ROOT / ".githooks"
    if hooks_dir.is_dir():
        files.extend(str(p) for p in hooks_dir.iterdir() if p.is_file())
    return sorted(set(files))


def _find_ps1_scripts() -> list[str]:
    """Discover PowerShell scripts to lint (excludes vendored venv and sysroot paths)."""
    return sorted(
        str(p)
        for p in _REPO_ROOT.rglob("*.ps1")
        if ".venv" not in p.parts and "venv" not in p.parts and "sysroot" not in p.parts
    )


def shell_lint() -> int:
    """Check shell script formatting (shfmt) and correctness (shellcheck).

    Also runs PSScriptAnalyzer on .ps1 files if available.
    """
    bash_files = _find_bash_scripts()
    ps1_files = _find_ps1_scripts()
    rc = 0

    # shfmt: check formatting (diff mode, 4-space indent, case indent)
    # shfmt --diff exits 0 on no diff, 1 when diffs are found or on I/O/parse errors;
    # we treat both cases the same — fail the lint step in either scenario.
    if bash_files:
        shfmt_path = shutil.which("shfmt")
        if shfmt_path is None:
            print("shfmt not found — skipping shell formatting checks")
            rc = 1
        else:
            print_step = f"> shfmt --diff -i 4 -ci {' '.join(bash_files)}"
            print(print_step)
            code = subprocess.call(
                [shfmt_path, "--diff", "-i", "4", "-ci", *bash_files]
            )
            if code != 0:
                rc = 1

    # shellcheck
    if bash_files:
        shellcheck_path = shutil.which("shellcheck")
        if shellcheck_path is None:
            print("shellcheck not found — skipping shell correctness checks")
            rc = 1
        else:
            print_step = f"> shellcheck {' '.join(bash_files)}"
            print(print_step)
            code = subprocess.call([shellcheck_path, *bash_files])
            if code != 0:
                rc = 1

    # PSScriptAnalyzer (optional — skip gracefully if not installed)
    # Invoke-ScriptAnalyzer always exits 0 — even when findings are reported —
    # so we capture findings into a variable and exit 1 when any are found.
    if ps1_files:
        print(f"> PSScriptAnalyzer {' '.join(ps1_files)}")
        for ps1 in ps1_files:
            try:
                ps1_escaped = ps1.replace("'", "''")
                command = (
                    "if (Get-Module -ListAvailable PSScriptAnalyzer) {"
                    f" $findings = Invoke-ScriptAnalyzer -Path '{ps1_escaped}' -Severity Warning;"
                    " if ($findings) { $findings | Format-List; exit 1 }"
                    f"}} else {{ Write-Host 'PSScriptAnalyzer not installed — skipping {ps1_escaped}' }}"
                )
                result = subprocess.run(
                    [
                        "pwsh",
                        "-NoProfile",
                        "-Command",
                        command,
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.stdout.strip():
                    print(result.stdout)
                if result.stderr.strip():
                    print(result.stderr, file=sys.stderr)
                if result.returncode != 0:
                    rc = 1
            except FileNotFoundError:
                print("pwsh not found — skipping PSScriptAnalyzer checks")
                break

    return rc


def shell_format() -> int:
    """Auto-fix shell script formatting with shfmt."""
    bash_files = _find_bash_scripts()
    if not bash_files:
        print("No bash scripts found.")
        return 0
    return _run("shfmt", "-w", "-i", "4", "-ci", *bash_files)


def yaml_lint() -> int:
    """Lint YAML files with yamllint."""
    yml_files = sorted(
        str(p)
        for p in _REPO_ROOT.rglob("*.yml")
        if ".venv" not in p.parts and "venv" not in p.parts
    )
    if not yml_files:
        print("No YAML files found.")
        return 0
    return _run(
        sys.executable,
        "-m",
        "yamllint",
        "-c",
        str(_REPO_ROOT / ".yamllint.yml"),
        *yml_files,
    )


VERSION_REFS: list[tuple[str, str, str, int]] = [
    # (filepath, pattern, replacement_template, re_flags)
    # pattern must have a group so replacement can anchor to context
    #
    # Note: templates/z.sh, templates/z.ps1, and templates/nanvix-ci.yml
    # use a {{ZUTIL_VERSION}} placeholder that is stamped by the release
    # workflow — they are NOT bumped here.
]


def version() -> int:
    """Bump the version across pyproject.toml and all template files.

    Usage:
        uv run tasks.py version <bump>            # bump = major | minor | patch | ...
        uv run tasks.py version <bump> --dry-run   # preview changes without writing
    """
    if len(sys.argv) < 3:
        print("Usage: uv run tasks.py version <bump> [--dry-run]")
        print("  bump: major | minor | patch | alpha | beta | rc | post | dev | stable")
        return 2

    uv = shutil.which("uv")
    if uv is None:
        print("error: 'uv' not found on PATH. Install it from https://astral.sh/uv")
        return 1

    bump = sys.argv[2]
    if bump.startswith("--"):
        print(f"error: expected bump type, got flag '{bump}'")
        print("Usage: uv run tasks.py version <bump> [--dry-run]")
        return 2
    dry_run = "--dry-run" in sys.argv[3:]

    # Read current version from pyproject.toml
    with (_REPO_ROOT / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    try:
        current: str = data["project"]["version"]
    except KeyError as exc:
        print(f"error: pyproject.toml missing key: {exc}")
        return 1

    if dry_run:
        result = subprocess.run(
            [uv, "version", "--bump", bump, "--dry-run", "--short"],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        )
        if result.returncode != 0:
            print(f"error: uv version failed: {result.stderr.strip()}")
            return 1
        new = result.stdout.strip()

        print(f"Version: {current} -> {new}")
        print("Files that would be updated:")
        print(f"  pyproject.toml  (via uv version --bump {bump})")
        for filepath, _, _, _ in VERSION_REFS:
            print(f"  {filepath}")
        return 0

    # Apply bump to pyproject.toml (and uv.lock) via uv
    print("> uv version --bump", bump)
    result = subprocess.run(
        [uv, "version", "--bump", bump],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"error: uv version failed: {result.stderr.strip()}")
        return 1

    # Re-read the version from pyproject.toml to get the canonical new version.
    pyproject_path = _REPO_ROOT / "pyproject.toml"
    try:
        with pyproject_path.open("rb") as f:
            updated_data = tomllib.load(f)
        new: str = updated_data["project"]["version"]
    except (OSError, KeyError) as exc:
        print(f"error: failed to read new version from pyproject.toml: {exc}")
        return 1

    # Update all version references
    for filepath, pattern, repl_template, flags in VERSION_REFS:
        path = _REPO_ROOT / filepath
        content = path.read_text()
        replacement = repl_template.format(new=new)
        updated = re.sub(pattern, replacement, content, flags=flags)
        if updated == content:
            print(f"warning: no match in {filepath}")
            continue
        path.write_text(updated)
        print(f"  Updated {filepath}")

    # Sync bootstrapper templates into each example directory.
    templates_dir = _REPO_ROOT / "templates"
    examples_dir = _REPO_ROOT / "examples"
    bootstrappers = ["z", "z.sh", "z.ps1"]
    for example in sorted(examples_dir.iterdir()):
        if not example.is_dir():
            continue
        for name in bootstrappers:
            src = templates_dir / name
            if src.exists():
                shutil.copy(src, example / name)

    # Sync .nanvix directory templates into each example.
    nanvix_templates = [".gitignore"]
    for example in sorted(examples_dir.iterdir()):
        if not example.is_dir():
            continue
        nanvix_dir = example / ".nanvix"
        if not nanvix_dir.is_dir():
            continue
        for name in nanvix_templates:
            src = templates_dir / name
            if src.exists():
                shutil.copy(src, nanvix_dir / name)

    print(f"\nVersion bumped: {current} -> {new}")
    return 0


COMMANDS: dict[str, tuple[Callable[[], int], str]] = {
    "setup": (setup, "Configure git hooks and sync dev dependencies"),
    "lint": (
        lint,
        "Run all linters (black, shfmt, shellcheck, PSScriptAnalyzer, yamllint)",
    ),
    "format": (format_code, "Fix code formatting with black"),
    "typecheck": (typecheck, "Run strict type checking with basedpyright"),
    "test": (test, "Run the test suite with pytest"),
    "test-downstream": (test_downstream, "Run the downstream_tests unit tests"),
    "ci": (
        ci,
        "Run CI locally using gh act (requires Docker + nanvix toolchain image)",
    ),
    "clean": (clean, "Remove Python bytecode caches and build artifacts"),
    "release": (release, "Build distribution artifacts (wheel and sdist)"),
    "downstream": (downstream, "Run downstream consumer tests (auto-detects platform)"),
    "version": (version, "Bump version across pyproject.toml and templates"),
    "shell-lint": (shell_lint, "Check shell script formatting and correctness"),
    "shell-format": (shell_format, "Auto-fix shell script formatting with shfmt"),
    "yaml-lint": (yaml_lint, "Lint YAML files with yamllint"),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(2)

    fn, _ = COMMANDS[cmd]
    sys.exit(fn())


if __name__ == "__main__":
    main()
