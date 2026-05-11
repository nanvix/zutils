# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""
Development task runner for nanvix-zutil.

Usage: uv run tasks.py <command>

Commands:
  setup         Configure git hooks and sync dev dependencies
  lint          Run all linters (black, shfmt, shellcheck, PSScriptAnalyzer, yamllint)
  format        Fix code formatting with black
  typecheck     Run strict type checking with pyright
  test          Run the test suite with pytest
  ci            Run CI locally using gh act (requires Docker + nanvix toolchain image)
  clean         Remove Python bytecode caches and build artifacts
  release       Cut a new release (bump, validate, tag, push)
  shell-lint    Check shell script formatting and correctness
  shell-format  Auto-fix shell script formatting with shfmt
  yaml-lint     Lint YAML files with yamllint
"""

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
    """Run strict type checking with pyright."""
    return _run(sys.executable, "-m", "pyright", *SOURCES)


def test() -> int:
    """Run the test suite with pytest."""
    return _run(sys.executable, "-m", "pytest", "tests/", "-v")


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


def _read_version() -> str:
    """Read the current version from pyproject.toml."""
    with (_REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def _run_checked(
    *args: str,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command rooted at the repo directory, raising on failure."""
    print(f"> {' '.join(args)}")
    return subprocess.run(
        args,
        cwd=_REPO_ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )


def _step(label: str) -> None:
    """Print a release step header."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")


def _bump_version(uv: str, bump: str) -> None:
    """Bump pyproject.toml version via uv and sync example bootstrappers."""
    _run_checked(uv, "version", "--bump", bump)

    # Sync bootstrapper templates into each example directory.
    templates_dir = _REPO_ROOT / "templates"
    examples_dir = _REPO_ROOT / "examples"
    for example in sorted(examples_dir.iterdir()):
        if not example.is_dir():
            continue
        for name in ("z", "z.sh", "z.ps1"):
            src = templates_dir / name
            if src.exists():
                shutil.copy(src, example / name)
        nanvix_dir = example / ".nanvix"
        if nanvix_dir.is_dir():
            gi = templates_dir / ".gitignore"
            if gi.exists():
                shutil.copy(gi, nanvix_dir / ".gitignore")


def _patch_templates(version: str) -> None:
    """Replace version placeholders in template files and verify."""
    templates = _REPO_ROOT / "templates"

    # Replace {{ZUTIL_VERSION}}
    for name in ("z.sh", "z.ps1", "nanvix-ci.yml"):
        path = templates / name
        content = path.read_text()
        path.write_text(content.replace("{{ZUTIL_VERSION}}", version))

    # Fetch latest nanvix/workflows version and replace {{WORKFLOW_VERSION}}
    result = subprocess.run(
        [
            "gh",
            "release",
            "view",
            "--repo",
            "nanvix/workflows",
            "--json",
            "tagName",
            "-q",
            ".tagName",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=_REPO_ROOT,
    )
    wflow_ver = result.stdout.strip().lstrip("v")
    ci_yml = templates / "nanvix-ci.yml"
    content = ci_yml.read_text()
    ci_yml.write_text(content.replace("{{WORKFLOW_VERSION}}", wflow_ver))

    # Verify no unreplaced placeholders remain
    for path in templates.iterdir():
        if path.is_file():
            content = path.read_text()
            if "{{ZUTIL_VERSION}}" in content or "{{WORKFLOW_VERSION}}" in content:
                raise RuntimeError(f"Unreplaced placeholder in {path.name}")

    print(f"  Patched templates to {version} (workflows: {wflow_ver})")


def _package_templates() -> None:
    """Create template archives (.tar.gz and .zip) and restore originals."""
    dist = _REPO_ROOT / "dist"
    dist.mkdir(exist_ok=True)
    base = str(dist / "templates")
    shutil.make_archive(base, "gztar", str(_REPO_ROOT / "templates"))
    shutil.make_archive(base, "zip", str(_REPO_ROOT / "templates"))
    # Restore placeholders so the working tree stays clean
    _run_checked("git", "checkout", "--", "templates/")
    print("  Created dist/templates.tar.gz and dist/templates.zip")


def _create_github_release(version: str) -> None:
    """Create a GitHub release with build and template artifacts."""
    tag = f"v{version}"
    dist = _REPO_ROOT / "dist"
    template_names = {"templates.tar.gz", "templates.zip"}
    assets: list[str] = sorted(
        str(p)
        for p in (*dist.glob("*.whl"), *dist.glob("*.tar.gz"))
        if p.name not in template_names
    )
    assets.append(str(dist / "templates.tar.gz"))
    assets.append(str(dist / "templates.zip"))
    _run_checked(
        "gh",
        "release",
        "create",
        tag,
        *assets,
        "--title",
        f"nanvix-zutil {tag}",
        "--generate-notes",
    )


def _update_consumers() -> None:
    """Trigger the consumer-update workflow in nanvix/workflows."""
    _run_checked(
        "gh",
        "workflow",
        "run",
        "nanvix-update-zutils.yml",
        "--repo",
        "nanvix/workflows",
    )


def _check_preconditions(*, ci_mode: bool = False) -> None:
    """Verify we are on dev (and, outside CI, that the tree is clean)."""
    _step("Checking preconditions")
    branch = _run_checked(
        "git", "rev-parse", "--abbrev-ref", "HEAD", capture=True
    ).stdout.strip()
    if branch != "dev":
        raise SystemExit(f"error: must be on 'dev' branch (currently on '{branch}')")
    print("  Branch: dev")

    if not ci_mode:
        status = _run_checked(
            "git", "status", "--porcelain", capture=True
        ).stdout.strip()
        if status:
            raise SystemExit(
                f"error: working tree is not clean — commit or stash changes first\n{status}"
            )
        print("  Working tree: clean")


def _resolve_version(
    uv: str,
    bump: str,
) -> str:
    """Bump the version and return the new version string."""
    _step(f"Bumping version ({bump})")
    old = _read_version()
    _bump_version(uv, bump)
    version = _read_version()
    print(f"\n  {old} -> {version}")
    return version


def _validate(uv: str) -> None:
    """Run lint, typecheck, and tests."""
    _step("Running validation")
    _run_checked(uv, "run", "tasks.py", "lint")
    _run_checked(uv, "run", "tasks.py", "typecheck")
    _run_checked(uv, "run", "tasks.py", "test")


def _check_tag_available(tag: str) -> None:
    """Ensure the tag does not already exist on the remote."""
    _step("Checking tag availability")
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if result.stdout.strip():
        raise SystemExit(f"error: tag {tag} already exists on origin")
    print(f"  Tag {tag} is available")


def _build_artifacts(uv: str, version: str) -> None:
    """Build wheel/sdist, patch templates, and package template archives."""
    _step("Building wheel and sdist")
    _run_checked(uv, "build")

    _step("Patching template versions")
    _patch_templates(version)

    _step("Packaging templates")
    _package_templates()


def _commit_and_push(tag: str, *, ci_mode: bool) -> None:
    """Commit the version bump and push to dev (without tagging)."""
    _step(f"Committing and pushing {tag}")
    _run_checked("git", "add", "pyproject.toml", "uv.lock", "examples/")
    if ci_mode:
        _run_checked(
            "git",
            "commit",
            "-m",
            f"[build] F: Release {tag} [skip ci]",
        )
        _run_checked("git", "fetch", "origin", "dev")
        _run_checked("git", "rebase", "origin/dev")
    else:
        _run_checked("git", "commit", "-m", f"[build] E: Release {tag}")
    _run_checked("git", "push", "origin", "HEAD:dev")


def _tag_and_push(tag: str) -> None:
    """Create and push the release tag."""
    _step(f"Tagging {tag}")
    _run_checked("git", "tag", tag)
    _run_checked("git", "push", "origin", tag)


def release() -> int:
    """Cut a new release: bump version, validate, build, tag, and publish.

    Automates the full release process:
      1. Checks preconditions (on dev branch, clean working tree).
      2. Bumps the version in pyproject.toml (patch by default).
      3. Runs validation (lint, typecheck, tests).
      4. Ensures the tag does not already exist.
      5. In CI mode: builds wheel/sdist, patches and packages templates.
      6. Commits the version bump, creates and pushes the tag.
      7. In CI mode: creates the GitHub Release with artifacts.

    Usage:
        uv run tasks.py release                          # patch bump (default)
        uv run tasks.py release minor                    # minor bump
        uv run tasks.py release major                    # major bump
        uv run tasks.py release --dry-run                # preview without committing
        uv run tasks.py release --ci                     # CI mode: full build+publish
    """
    bump = "patch"
    dry_run = False
    ci_mode = False
    for arg in sys.argv[2:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--ci":
            ci_mode = True
        elif arg in ("major", "minor", "patch"):
            bump = arg
        else:
            print(f"error: unknown argument '{arg}'")
            print(
                "Usage: uv run tasks.py release [major|minor|patch] [--dry-run] [--ci]"
            )
            return 2

    if dry_run and ci_mode:
        print("error: --dry-run and --ci are mutually exclusive")
        return 2

    uv = shutil.which("uv")
    if uv is None:
        print("error: 'uv' not found on PATH. Install it from https://astral.sh/uv")
        return 1

    try:
        _check_preconditions(ci_mode=ci_mode)

        new = _resolve_version(uv, bump)
        tag = f"v{new}"

        if not ci_mode:
            _validate(uv)

        _check_tag_available(tag)

        if dry_run:
            _step("Dry run — skipping commit and push")
            print(f"  Would release {tag}")
            print("  Resetting version bump...")
            _run_checked("git", "checkout", "--", ".")
            return 0

        if ci_mode:
            _build_artifacts(uv, new)

        _commit_and_push(tag, ci_mode=ci_mode)

        if ci_mode:
            _step("Creating GitHub release")
            _create_github_release(new)

            _step("Updating consumer repos")
            _update_consumers()

        _tag_and_push(tag)

        _step("Done")
        print(f"  Released {tag} — pushed to dev.")
        if not ci_mode:
            print("  To publish, trigger the Release workflow on GitHub:")
            print("    gh workflow run release.yml -f bump=patch")
        return 0

    except subprocess.CalledProcessError as exc:
        print(f"\nerror: command failed with exit code {exc.returncode}")
        print(f"  {' '.join(str(a) for a in exc.cmd)}")
        return 1
    except KeyboardInterrupt:
        print("\n\nAborted.")
        return 130


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
    shfmt_path = shutil.which("shfmt")
    if shfmt_path is None:
        print("error: shfmt not found")
        return 1
    return _run(shfmt_path, "-w", "-i", "4", "-ci", *bash_files)


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


COMMANDS: dict[str, tuple[Callable[[], int], str]] = {
    "setup": (setup, "Configure git hooks and sync dev dependencies"),
    "lint": (
        lint,
        "Run all linters (black, shfmt, shellcheck, PSScriptAnalyzer, yamllint)",
    ),
    "format": (format_code, "Fix code formatting with black"),
    "typecheck": (typecheck, "Run strict type checking with pyright"),
    "test": (test, "Run the test suite with pytest"),
    "ci": (
        ci,
        "Run CI locally using gh act (requires Docker + nanvix toolchain image)",
    ),
    "clean": (clean, "Remove Python bytecode caches and build artifacts"),
    "release": (release, "Cut a new release (bump, validate, tag, push)"),
    "shell-lint": (shell_lint, "Check shell script formatting and correctness"),
    "shell-format": (shell_format, "Auto-fix shell script formatting with shfmt"),
    "yaml-lint": (yaml_lint, "Lint YAML files with yamllint"),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    if sys.argv[1] == "--version":
        print(_read_version())
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
