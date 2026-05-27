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
  clean         Remove Python bytecode caches and build artifacts
  release       Cut a new release (bump, validate, tag, push)
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
    new_version = _read_version()
    pinned_tag = f"v{new_version}\n"

    # Pin templates/.zutils-version so the shipped templates.tar.gz and the
    # in-tree examples agree with the just-bumped release. The file is
    # tracked and gets staged in `_commit_and_push`; `_package_templates`
    # narrows its restore to `templates/nanvix-ci.yml` so this write
    # survives the build round-trip.
    templates_dir = _REPO_ROOT / "templates"
    (templates_dir / ".zutils-version").write_text(pinned_tag)

    # Sync bootstrapper templates into each example directory.
    examples_dir = _REPO_ROOT / "examples"
    for example in sorted(examples_dir.iterdir()):
        if not example.is_dir():
            continue
        for name in ("z", "z.sh", "z.ps1"):
            src = templates_dir / name
            if src.exists():
                shutil.copy(src, example / name)
        # Pin .zutils-version to the freshly-bumped tag so in-tree examples
        # exercise the same source-of-truth file shipped to consumer repos.
        (example / ".zutils-version").write_text(pinned_tag)
        nanvix_dir = example / ".nanvix"
        if nanvix_dir.is_dir():
            gi = templates_dir / ".gitignore"
            if gi.exists():
                shutil.copy(gi, nanvix_dir / ".gitignore")


def _patch_templates() -> None:
    """Substitute `{{WORKFLOW_VERSION}}` in templates and verify no
    `{{...}}` placeholder residue remains.

    Note: `templates/.zutils-version` is maintained by `_bump_version` as a
    tracked file, not by this function.
    """
    templates = _REPO_ROOT / "templates"

    # Resolve the latest nanvix/workflows tag and stamp it into
    # `templates/nanvix-ci.yml`. We resolve at release time rather than
    # pinning a literal so the shipped workflow always references a
    # workflows release that exists upstream.
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
    ci_yml.write_text(
        ci_yml.read_text().replace("{{WORKFLOW_VERSION}}", wflow_ver)
    )

    # Verify no unreplaced `{{ANYTHING}}` placeholder remains in any
    # template file. The generic scan future-proofs against newly
    # introduced placeholders that someone forgets to wire into this
    # function.
    placeholder = re.compile(r"\{\{[A-Z_]+\}\}")
    for path in templates.iterdir():
        if not path.is_file():
            continue
        m = placeholder.search(path.read_text())
        if m:
            raise RuntimeError(
                f"Unreplaced placeholder {m.group(0)} in {path.name}"
            )

    print(f"  Patched templates (workflows: {wflow_ver})")


def _package_templates() -> None:
    """Create template archives (.tar.gz and .zip) and restore originals."""
    dist = _REPO_ROOT / "dist"
    dist.mkdir(exist_ok=True)
    base = str(dist / "templates")
    try:
        shutil.make_archive(base, "gztar", str(_REPO_ROOT / "templates"))
        shutil.make_archive(base, "zip", str(_REPO_ROOT / "templates"))
    finally:
        # Restore only the file we substituted into. A broader
        # `git checkout -- templates/` would also revert the bumped
        # `templates/.zutils-version` (tracked) written by `_bump_version`,
        # which `_commit_and_push` needs to stage.
        _run_checked("git", "checkout", "--", "templates/nanvix-ci.yml")
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
    del version  # currently unused; kept for API stability
    _step("Building wheel and sdist")
    _run_checked(uv, "build")

    _step("Patching template versions")
    _patch_templates()

    _step("Packaging templates")
    _package_templates()


def _commit_and_push(tag: str, *, ci_mode: bool) -> None:
    """Commit the version bump and push to dev (without tagging)."""
    _step(f"Committing and pushing {tag}")
    _run_checked(
        "git",
        "add",
        "pyproject.toml",
        "uv.lock",
        "examples/",
        "templates/.zutils-version",
    )
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
