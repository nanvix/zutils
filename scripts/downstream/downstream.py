"""downstream.py — Validate nanvix-zutil against downstream consumers.

Builds a wheel from the current branch, resolves consumer repos using
configurable checkout strategies (bare/clone/shallow), installs the wheel
into a fresh venv per consumer, and runs setup / build / test phases.
Cross-platform (Linux and Windows) with stdlib only.

Usage:
    python downstream.py --platform {linux,windows} [options] [consumer...]

--platform is REQUIRED.  It is set by wrapper.py, not by the end user.

Options:
    --platform {linux,windows}  Platform to run on (required)
    --config FILE               Path to downstream.json
                                (default: <script_dir>/downstream.json)
    --setup-only                Only run the setup phase
    --skip-build                Skip wheel build; reuse existing wheel
    --force-fallback            Force dependency-version fallback (implies
                                --setup-only)
    --with-docker               Pass --with-docker to nanvix-zutil build/test
    --dry-run                   Print what would happen without executing

Positional:
    consumers                   Owner/repo names to test (default: all from
                                config)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONSUMERS_URL = (
    "https://raw.githubusercontent.com/nanvix/workflows/refs/heads/main/"
    "consumer-repos.json"
)
CONSUMER_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")

# Shim for os.getuid / os.getgid: on Windows these don't exist, which
# crashes nanvix-zutil at import time.  The getattr is a no-op on Linux
# (returns the real function), so we always apply it for simplicity.
SHIM = (
    "import os,sys;"
    "os.getuid=getattr(os,'getuid',lambda:0);"
    "os.getgid=getattr(os,'getgid',lambda:0)"
)

_SCRIPT_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    """Print an informational message with a blue bold prefix."""
    print(f"\033[1;34m>>>\033[0m {msg}")


def ok(msg: str) -> None:
    """Print a success message with a green bold prefix."""
    print(f"\033[1;32m OK\033[0m {msg}")


def fail(msg: str) -> None:
    """Print a failure message with a red bold prefix."""
    print(f"\033[1;31mFAIL\033[0m {msg}")


def dry(msg: str) -> None:
    """Print a dry-run notice with a yellow bold prefix."""
    print(f"\033[1;33m[dry-run]\033[0m {msg}")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def ensure_config(
    config_path: Path,
    cache_path: Path,
    *,
    dry_run: bool = False,
) -> Path:
    """Ensure downstream.json exists, generating it from consumer-repos.json.

    Fetch consumer-repos.json from the remote URL and cache it locally.
    Transform the list into a downstream.json structure.  In dry-run mode,
    write to a temporary file and return that path so the rest of the script
    can still read a valid config.

    Args:
        config_path: Desired path for downstream.json.
        cache_path:  Path for the cached consumer-repos.json.
        dry_run:     When True, write the generated config to a temp file
                     instead of *config_path*.

    Returns:
        The effective config path (may be a temp file in dry-run mode).

    Raises:
        RuntimeError: If the remote fetch fails and no cache is available.
    """
    if config_path.exists():
        return config_path

    log("No downstream.json found — generating from consumer-repos.json...")

    repos: list[str] = []
    fetched = False

    try:
        with urllib.request.urlopen(CONSUMERS_URL, timeout=30) as resp:
            raw = resp.read().decode()
        repos = json.loads(raw)
        if not dry_run:
            cache_path.write_text(raw, encoding="utf-8")
        log("  Fetched consumer list from remote")
        fetched = True
    except Exception:
        pass

    if not fetched:
        if cache_path.exists():
            repos = json.loads(cache_path.read_text(encoding="utf-8"))
            log("  Using cached consumer-repos.json")
        else:
            raise RuntimeError(
                f"Cannot fetch consumer list and no cache at {cache_path}"
            )

    config_data = {
        "$schema": "./downstream.schema.json",
        "defaults": {
            "checkout_strategy": "shallow",
            "repos_root": "~/repos",
            "win_repos_root": None,
            "branch_pattern": "nanvix/v*",
        },
        "consumers": [{"repo": r} for r in repos],
    }
    config_json = json.dumps(config_data, indent=2)

    if dry_run:
        dry(f"would generate {config_path} from consumer-repos.json")
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        )
        tmp.write(config_json)
        tmp.close()
        return Path(tmp.name)

    config_path.write_text(config_json, encoding="utf-8")
    log(f"Generated {config_path} — customize as needed.")
    return config_path


def load_config(config_path: Path) -> dict:
    """Read and parse downstream.json, applying defaults for missing keys.

    Expands ``~`` in ``repos_root``.  Sets sensible defaults for any key
    absent from the ``defaults`` section.

    Args:
        config_path: Path to downstream.json.

    Returns:
        Parsed config dict with defaults filled in.
    """
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    defaults = raw.setdefault("defaults", {})

    repos_root = defaults.get("repos_root", "~/repos")
    defaults["repos_root"] = str(Path(repos_root).expanduser())

    defaults.setdefault("checkout_strategy", "shallow")
    defaults.setdefault("win_repos_root", None)
    defaults.setdefault("branch_pattern", "nanvix/v*")

    raw.setdefault("consumers", [])
    return raw


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_consumer(name: str) -> bool:
    """Validate that *name* matches the ``owner/repo`` pattern.

    Rejects names that could cause shell injection or path traversal.

    Args:
        name: Consumer name to validate.

    Returns:
        True if valid, False otherwise.
    """
    return bool(CONSUMER_RE.match(name))


# ---------------------------------------------------------------------------
# Checkout helpers
# ---------------------------------------------------------------------------


def detect_strategy(repo_path: Path) -> str:
    """Auto-detect the checkout strategy for an existing repo path.

    Logic (mirrors bash detect_checkout_strategy):
    - Non-existent path → "shallow"
    - Has HEAD file but no .git directory → "bare"
    - .git is a regular file (gitdir pointer / worktree) → "bare"
    - .git is a directory → "clone"
    - Anything else → "shallow"

    Args:
        repo_path: Filesystem path to inspect.

    Returns:
        One of ``"bare"``, ``"clone"``, or ``"shallow"``.
    """
    if not repo_path.exists():
        return "shallow"
    head_file = repo_path / "HEAD"
    git_entry = repo_path / ".git"
    if head_file.is_file() and not git_entry.is_dir():
        return "bare"
    if git_entry.is_file():
        # .git is a file → gitdir pointer → worktree of a bare repo
        return "bare"
    if git_entry.is_dir():
        return "clone"
    return "shallow"


def resolve_branch(
    consumer: str,
    repo_path: Path,
    strategy: str,
    pattern: str,
) -> Optional[str]:
    """Resolve the target branch for a consumer repo.

    For bare repos that already exist locally, query local refs via
    ``git for-each-ref``.  For clone/shallow (or non-existent paths),
    use ``git ls-remote --heads``.  Falls back to the default branch via
    ``git ls-remote --symref HEAD`` when no pattern match is found.

    Args:
        consumer:  GitHub ``owner/repo`` slug.
        repo_path: Local filesystem path of the repo (may not exist yet).
        strategy:  Checkout strategy (``"bare"``, ``"clone"``, ``"shallow"``).
        pattern:   Branch glob pattern, e.g. ``"nanvix/v*"``.

    Returns:
        Resolved branch name, or None if it cannot be determined.
    """
    target_ref: Optional[str] = None
    remote_url = f"https://github.com/{consumer}.git"

    if strategy == "bare" and repo_path.is_dir():
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "for-each-ref",
                "--sort=version:refname",
                "--format=%(refname:short)",
                f"refs/heads/{pattern}",
                f"refs/remotes/origin/{pattern}",
            ],
            capture_output=True,
            text=True,
        )
        refs = [r.strip() for r in result.stdout.splitlines() if r.strip()]
        if refs:
            raw_ref = refs[-1]
            target_ref = raw_ref.removeprefix("origin/")

        if not target_ref:
            sym = subprocess.run(
                ["git", "-C", str(repo_path), "symbolic-ref", "HEAD"],
                capture_output=True,
                text=True,
            )
            symref = sym.stdout.strip()
            if symref.startswith("refs/heads/"):
                target_ref = symref.removeprefix("refs/heads/")
    else:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", remote_url, pattern],
            capture_output=True,
            text=True,
        )
        branches: list[str] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                ref = parts[1].strip()
                if ref.startswith("refs/heads/"):
                    branches.append(ref.removeprefix("refs/heads/"))
        if branches:
            # Sort by version and take the last (highest).
            try:
                branches.sort(
                    key=lambda b: [
                        int(x) if x.isdigit() else x
                        for x in re.split(r"[\./]", b)
                    ]
                )
            except Exception:
                pass
            target_ref = branches[-1]

        if not target_ref:
            sym = subprocess.run(
                ["git", "ls-remote", "--symref", remote_url, "HEAD"],
                capture_output=True,
                text=True,
            )
            for line in sym.stdout.splitlines():
                if line.startswith("ref:"):
                    parts = line.split()
                    if parts:
                        symref = parts[1] if len(parts) > 1 else ""
                        if symref.startswith("refs/heads/"):
                            target_ref = symref.removeprefix("refs/heads/")
                    break

    return target_ref or None


def _resolve_bare(
    consumer: str,
    repo_path: Path,
    branch: str,
) -> Optional[Path]:
    """Resolve a consumer repo using the bare + worktree strategy.

    Clones as a bare repo if not present, ensures the fetch refspec is
    correct, fetches latest, then creates or reuses a worktree for
    *branch*.

    Args:
        consumer:  GitHub ``owner/repo`` slug.
        repo_path: Expected local path of the bare repo.
        branch:    Target branch to check out as a worktree.

    Returns:
        Path to the worktree directory, or None on failure.
    """
    clone_url = f"https://github.com/{consumer}.git"

    if not repo_path.is_dir():
        log(f"  {consumer}: cloning bare repo to {repo_path}")
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--bare", clone_url, str(repo_path)]
        )
        if result.returncode != 0:
            fail(f"  {consumer}: git clone --bare failed")
            return None
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "config",
                "remote.origin.fetch",
                "+refs/heads/*:refs/remotes/origin/*",
            ]
        )
        ok(f"  {consumer}: cloned")

    # Ensure fetch refspec is correct.
    cur = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "config",
            "--get",
            "remote.origin.fetch",
        ],
        capture_output=True,
        text=True,
    )
    if cur.stdout.strip() != "+refs/heads/*:refs/remotes/origin/*":
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "config",
                "remote.origin.fetch",
                "+refs/heads/*:refs/remotes/origin/*",
            ]
        )

    log(f"  {consumer}: fetching latest")
    subprocess.run(
        ["git", "-C", str(repo_path), "fetch", "origin", "--prune"],
        check=False,
    )

    # Look for existing nanvix/v* worktree directories.
    nanvix_dir = repo_path / "nanvix"
    if nanvix_dir.is_dir():
        v_dirs = sorted(
            [d for d in nanvix_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
            key=lambda d: d.name,
        )
        if v_dirs:
            wt_dir = v_dirs[-1]
            cur_branch_r = subprocess.run(
                ["git", "-C", str(wt_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
            )
            cur_branch = cur_branch_r.stdout.strip()
            log(f"  {consumer}: updating worktree at {wt_dir}")
            subprocess.run(
                ["git", "-C", str(wt_dir), "fetch", "origin"], check=False
            )
            if cur_branch:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(wt_dir),
                        "reset",
                        "--hard",
                        f"origin/{cur_branch}",
                    ],
                    check=False,
                )
            return wt_dir

    wt_dir = repo_path / branch

    if wt_dir.is_dir():
        cur_branch_r = subprocess.run(
            ["git", "-C", str(wt_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        )
        cur_branch = cur_branch_r.stdout.strip()
        log(f"  {consumer}: updating worktree at {wt_dir}")
        subprocess.run(["git", "-C", str(wt_dir), "fetch", "origin"], check=False)
        if cur_branch:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(wt_dir),
                    "reset",
                    "--hard",
                    f"origin/{cur_branch}",
                ],
                check=False,
            )
        return wt_dir

    log(f"  {consumer}: creating worktree for {branch}")
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", str(wt_dir), branch]
    )
    return wt_dir


def _resolve_clone(
    consumer: str,
    repo_path: Path,
    branch: str,
) -> Optional[Path]:
    """Resolve a consumer repo using the standard clone strategy.

    Clones the repo if not present, then fetches and checks out *branch*.

    Args:
        consumer:  GitHub ``owner/repo`` slug.
        repo_path: Expected local path of the clone.
        branch:    Target branch to check out.

    Returns:
        Path to the repo directory, or None on failure.
    """
    clone_url = f"https://github.com/{consumer}.git"

    if not repo_path.is_dir():
        log(f"  {consumer}: cloning to {repo_path}")
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "clone", clone_url, str(repo_path)])
        if result.returncode != 0:
            fail(f"  {consumer}: git clone failed")
            return None
        ok(f"  {consumer}: cloned")

    log(f"  {consumer}: fetching and checking out {branch}")
    subprocess.run(
        ["git", "-C", str(repo_path), "fetch", "origin"], check=False
    )
    co = subprocess.run(
        ["git", "-C", str(repo_path), "checkout", branch],
        capture_output=True,
    )
    if co.returncode != 0:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "checkout",
                "-b",
                branch,
                f"origin/{branch}",
            ]
        )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "reset",
            "--hard",
            f"origin/{branch}",
        ],
        check=False,
    )
    return repo_path


def _resolve_shallow(
    consumer: str,
    repo_path: Path,
    branch: str,
) -> Optional[Path]:
    """Resolve a consumer repo using the shallow clone strategy.

    Performs a depth-1 clone if the repo does not exist locally, otherwise
    fetches the latest shallow copy of *branch* and resets.

    Args:
        consumer:  GitHub ``owner/repo`` slug.
        repo_path: Expected local path of the shallow clone.
        branch:    Target branch to clone / update.

    Returns:
        Path to the repo directory, or None on failure.
    """
    clone_url = f"https://github.com/{consumer}.git"

    if not repo_path.is_dir():
        log(f"  {consumer}: shallow clone ({branch}) to {repo_path}")
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "-b", branch, clone_url, str(repo_path)]
        )
        if result.returncode != 0:
            fail(f"  {consumer}: shallow clone failed")
            return None
        ok(f"  {consumer}: cloned (shallow)")
        return repo_path

    log(f"  {consumer}: updating shallow clone")
    subprocess.run(
        ["git", "-C", str(repo_path), "fetch", "--depth", "1", "origin", branch],
        check=False,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "reset",
            "--hard",
            f"origin/{branch}",
        ],
        check=False,
    )
    return repo_path


def resolve_repo(
    consumer: str,
    repos_root: Path,
    strategy: str = "",
    branch: str = "",
    pattern: str = "nanvix/v*",
    *,
    dry_run: bool = False,
) -> Optional[Path]:
    """Resolve a consumer repo to a working-directory path.

    Auto-detects strategy and branch when not supplied.  In dry-run mode,
    prints what would happen and returns the expected path without executing
    any git commands.

    Args:
        consumer:   GitHub ``owner/repo`` slug.
        repos_root: Root directory where consumer repos are stored locally.
        strategy:   Checkout strategy override (``"bare"``, ``"clone"``,
                    ``"shallow"``); auto-detected if empty.
        branch:     Branch override; resolved via git if empty.
        pattern:    Branch glob pattern used during branch resolution.
        dry_run:    When True, skip git operations and return a placeholder.

    Returns:
        Path to the resolved working directory, or None on failure.
    """
    repo_path = repos_root / consumer

    if not strategy:
        strategy = detect_strategy(repo_path)
        log(f"  {consumer}: auto-detected strategy: {strategy}")

    if not branch:
        if dry_run:
            branch = pattern.replace("*", "vX.Y.Z")
        else:
            branch = resolve_branch(consumer, repo_path, strategy, pattern) or ""

    if not branch:
        fail(f"  {consumer}: cannot determine target branch")
        return None

    log(f"  {consumer}: strategy={strategy} branch={branch}")

    if dry_run:
        target_dir = (repo_path / branch) if strategy == "bare" else repo_path
        dry(f"  {consumer}: would resolve via '{strategy}' strategy")
        if not repo_path.exists():
            dry(f"  {consumer}: would clone https://github.com/{consumer}.git → {repo_path}")
        else:
            dry(f"  {consumer}: would fetch + update at {repo_path}")
        dry(f"  {consumer}: working directory → {target_dir}")
        return target_dir

    if strategy == "bare":
        return _resolve_bare(consumer, repo_path, branch)
    if strategy == "clone":
        return _resolve_clone(consumer, repo_path, branch)
    if strategy == "shallow":
        return _resolve_shallow(consumer, repo_path, branch)

    fail(f"  {consumer}: unknown checkout strategy: {strategy}")
    return None


# ---------------------------------------------------------------------------
# Wheel build
# ---------------------------------------------------------------------------


def build_wheel(
    zutils_root: Path,
    work_dir: Path,
    *,
    skip_build: bool = False,
    dry_run: bool = False,
) -> Path:
    """Build a nanvix-zutil wheel into ``work_dir/wheel/``.

    Tries ``pip``, then ``uv pip``, then ``python -m pip`` in order.

    Args:
        zutils_root: Root of the nanvix-zutil source tree.
        work_dir:    Scratch directory; the wheel is placed in
                     ``work_dir/wheel/``.
        skip_build:  When True, reuse an existing ``.whl`` file.
        dry_run:     When True, return a placeholder path without building.

    Returns:
        Path to the wheel file.

    Raises:
        SystemExit: On failure to build or find a wheel.
    """
    wheel_dir = work_dir / "wheel"

    if dry_run:
        dry(f"would build wheel from {zutils_root} → {wheel_dir}")
        return wheel_dir / "nanvix_zutil-dry_run-py3-none-any.whl"

    if skip_build:
        existing = list(wheel_dir.glob("*.whl"))
        if existing:
            ok(f"Reusing: {existing[0].name}")
            return existing[0]
        fail(f"No wheel found in {wheel_dir}. Run without --skip-build first.")
        sys.exit(1)

    log(f"Building nanvix-zutil wheel from {zutils_root}")
    wheel_dir.mkdir(parents=True, exist_ok=True)
    for whl in wheel_dir.glob("*.whl"):
        whl.unlink()

    pip_cmd: Optional[list[str]] = None
    if shutil.which("pip"):
        pip_cmd = ["pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(zutils_root)]
    elif shutil.which("uv"):
        pip_cmd = ["uv", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(zutils_root)]
    else:
        pip_cmd = [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(zutils_root),
        ]

    subprocess.run(pip_cmd, check=True)

    wheels = list(wheel_dir.glob("*.whl"))
    if not wheels:
        fail("Wheel build produced no .whl file")
        sys.exit(1)

    ok(f"Built: {wheels[0].name}")
    return wheels[0]


# ---------------------------------------------------------------------------
# Fallback env
# ---------------------------------------------------------------------------


def export_fallback_env(repo_dir: Path) -> None:
    """Parse ``.nanvix/nanvix.toml`` and set NANVIX_VERSION_* env vars.

    Reads the ``[dependencies]`` section and for each entry sets
    ``NANVIX_VERSION_<NAME>`` to ``<version>-nanvix-99.99.99``.  This forces
    ``resolve_release_with_fallback()`` to miss the exact tag and fall back to
    the best available release.

    Args:
        repo_dir: Root of the consumer repo.

    Raises:
        RuntimeError: If ``.nanvix/nanvix.toml`` does not exist.
    """
    manifest = repo_dir / ".nanvix" / "nanvix.toml"
    if not manifest.exists():
        raise RuntimeError(f"  No nanvix.toml at {manifest}")

    in_deps = False
    dep_re = re.compile(r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]+)"')
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("[dependencies]"):
            in_deps = True
            continue
        if line.strip().startswith("["):
            in_deps = False
            continue
        if in_deps:
            m = dep_re.match(line)
            if m:
                raw_name = m.group(1)
                version = m.group(2)
                env_key = "NANVIX_VERSION_" + raw_name.upper().replace("-", "_")
                env_val = f"{version}-nanvix-99.99.99"
                os.environ[env_key] = env_val
                log(f"  {env_key}={env_val}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_consumer(
    consumer: str,
    repo_dir: Path,
    wheel_path: Path,
    *,
    setup_only: bool,
    force_fallback: bool,
    with_docker: bool,
    dry_run: bool,
) -> tuple[str, str]:
    """Run setup / build / test phases for one consumer.

    Creates a fresh venv inside ``repo_dir/.nanvix/venv``, installs the
    wheel, then runs the requested phases.  nanvix-zutil is invoked via
    a compatibility shim (no-op on Linux, patches os.getuid/getgid on
    Windows).

    Args:
        consumer:       GitHub ``owner/repo`` slug (used only for log output).
        repo_dir:       Working directory of the consumer repo.
        wheel_path:     Path to the nanvix-zutil wheel to install.
        setup_only:     Skip build and test phases.
        force_fallback: Force dependency fallback and assert it is triggered.
        with_docker:    Pass ``--with-docker`` to build / test phases.
        dry_run:        Print what would happen without executing.

    Returns:
        A ``(consumer, status)`` tuple where *status* is a human-readable
        result string, e.g. ``"OK (setup,build,test)"`` or
        ``"FAIL (setup)"``.
    """
    venv_dir = repo_dir / ".nanvix" / "venv"

    if dry_run:
        dry(f"  {consumer}: would create venv at {venv_dir}")
        dry(f"  {consumer}: would install wheel {wheel_path}")
        if force_fallback:
            dry(f"  {consumer}: would force dependency fallback")
        dry(f"  {consumer}: would run: nanvix-zutil setup")
        if not setup_only:
            docker_str = "--with-docker " if with_docker else ""
            dry(f"  {consumer}: would run: nanvix-zutil {docker_str}build")
            dry(f"  {consumer}: would run: nanvix-zutil {docker_str}test")
        return consumer, "OK (dry-run)"

    # --- venv creation ---------------------------------------------------------
    if venv_dir.exists():
        shutil.rmtree(venv_dir)

    log("  Creating venv and installing local wheel...")
    r = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        fail(f"  {consumer}: venv creation failed")
        return consumer, "FAIL (venv)"

    # Locate python inside the venv.
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        venv_python = venv_dir / "Scripts" / "python"

    # Install wheel.
    pip_r = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(wheel_path)],
        capture_output=True,
        text=True,
    )
    if pip_r.returncode != 0:
        # Try ensurepip first.
        subprocess.run(
            [str(venv_python), "-m", "ensurepip", "--default-pip"],
            capture_output=True,
        )
        pip_r2 = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet", str(wheel_path)],
            capture_output=True,
            text=True,
        )
        if pip_r2.returncode != 0:
            fail(f"  {consumer}: wheel install failed")
            return consumer, "FAIL (pip install)"

    ver = subprocess.run(
        [str(venv_python), "-c", "import nanvix_zutil; print('OK')"],
        capture_output=True,
        text=True,
    )
    log(f"  nanvix_zutil import: {ver.stdout.strip()}")

    # --- Force fallback setup --------------------------------------------------
    if force_fallback:
        log("  Forcing dependency fallback...")
        buildroot = repo_dir / ".nanvix" / "buildroot"
        cache = repo_dir / ".nanvix" / "cache"
        if buildroot.exists():
            shutil.rmtree(buildroot)
        if cache.exists():
            shutil.rmtree(cache)
        log("  Cleaned buildroot and cache")
        export_fallback_env(repo_dir)

    # --- Phase 1: setup --------------------------------------------------------
    log("  Running: nanvix-zutil setup")
    setup_cmd = [
        str(venv_python),
        "-c",
        f"{SHIM};from nanvix_zutil.__main__ import main;sys.exit(main())",
        "setup",
    ]

    setup_result = subprocess.run(
        setup_cmd,
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    setup_output = setup_result.stdout + setup_result.stderr
    print(setup_output, end="")
    setup_rc = setup_result.returncode

    if force_fallback:
        fallback_detected = setup_rc == 7 or bool(
            re.search(r"fallback for", setup_output, re.IGNORECASE)
        )
        if fallback_detected:
            ok(f"  {consumer} setup: fallback detected (exit {setup_rc})")
            return consumer, "OK (fallback verified)"
        if setup_rc == 0:
            fail(f"  {consumer} setup: no fallback detected (exit 0, no fallback log)")
            return consumer, "FAIL (fallback not triggered)"
        fail(f"  {consumer} setup: unexpected exit {setup_rc} (no fallback log)")
        return consumer, f"FAIL (setup exit {setup_rc})"

    if setup_rc != 0:
        fail(f"  {consumer} setup failed")
        return consumer, "FAIL (setup)"
    ok(f"  {consumer} setup: OK")

    if setup_only:
        log("  (skipping build/test — setup-only mode)")
        return consumer, "OK (setup)"

    # --- Docker availability check ---------------------------------------------
    docker_flag: list[str] = []
    if with_docker:
        if not shutil.which("docker"):
            log("  --with-docker requested but Docker not available — skipping build/test")
            return consumer, "OK (setup, no docker)"
        docker_flag = ["--with-docker"]

    # --- Phase 2: build --------------------------------------------------------
    build_cmd = [
        str(venv_python),
        "-c",
        f"{SHIM};from nanvix_zutil.__main__ import main;sys.exit(main())",
        *docker_flag,
        "build",
    ]

    log(f"  Running: nanvix-zutil {' '.join(docker_flag)} build".rstrip())
    build_result = subprocess.run(
        build_cmd,
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    print(build_result.stdout + build_result.stderr, end="")
    if build_result.returncode != 0:
        fail(f"  {consumer} build failed")
        return consumer, "FAIL (build)"
    ok(f"  {consumer} build: OK")

    # --- Phase 3: test ---------------------------------------------------------
    test_cmd = [
        str(venv_python),
        "-c",
        f"{SHIM};from nanvix_zutil.__main__ import main;sys.exit(main())",
        *docker_flag,
        "test",
    ]

    log(f"  Running: nanvix-zutil {' '.join(docker_flag)} test".rstrip())
    test_result = subprocess.run(
        test_cmd,
        capture_output=True,
        text=True,
        cwd=str(repo_dir),
    )
    print(test_result.stdout + test_result.stderr, end="")
    if test_result.returncode != 0:
        fail(f"  {consumer} test failed")
        return consumer, "FAIL (test)"
    ok(f"  {consumer} test: OK")

    return consumer, "OK (setup,build,test)"


def run_platform(
    consumers: list[dict],
    config: dict,
    wheel_path: Path,
    *,
    platform: str,
    setup_only: bool,
    force_fallback: bool,
    with_docker: bool,
    dry_run: bool,
    skip_build: bool,
) -> int:
    """Iterate consumers, resolve repos, run phases, and report results.

    Args:
        consumers:      List of consumer dicts from the config (each has at
                        minimum a ``"repo"`` key).
        config:         Full config dict from :func:`load_config`.
        wheel_path:     Path to the built wheel.
        platform:       ``"linux"`` or ``"windows"``.
        setup_only:     Skip build and test phases.
        force_fallback: Force dependency fallback for all consumers.
        with_docker:    Pass ``--with-docker`` to build / test commands.
        dry_run:        Skip real operations; print what would happen.
        skip_build:     Unused here; kept for API consistency with callers.

    Returns:
        Number of consumers that failed.
    """
    is_windows = platform == "windows"
    defaults = config.get("defaults", {})
    repos_root_str = defaults.get("repos_root", "~/repos")

    if is_windows:
        win_repos_root = defaults.get("win_repos_root") or ""
        if not win_repos_root:
            # Auto-detect via cmd.exe / wslpath.
            try:
                wp = subprocess.run(
                    ["cmd.exe", "/C", "echo %USERPROFILE%"],
                    capture_output=True,
                    text=True,
                )
                win_userprofile = wp.stdout.strip()
                if win_userprofile:
                    wp2 = subprocess.run(
                        ["wslpath", "-u", win_userprofile],
                        capture_output=True,
                        text=True,
                    )
                    win_home = wp2.stdout.strip()
                    win_repos_root = str(Path(win_home) / "repos") if win_home else ""
            except Exception:
                pass
        repos_root = Path(win_repos_root or repos_root_str).expanduser()
    else:
        repos_root = Path(repos_root_str).expanduser()

    default_strategy = defaults.get("checkout_strategy", "shallow")
    branch_pattern = defaults.get("branch_pattern", "nanvix/v*")

    platform_label = "Windows" if is_windows else "Linux"
    log(f"=== {platform_label} Downstream Test ===")
    print()
    log(f"Wheel: {wheel_path}")
    log(f"Repos root: {repos_root}")
    log(f"Consumers: {', '.join(c['repo'] for c in consumers)}")
    log(f"Setup only: {setup_only}")
    print()

    results: list[tuple[str, str]] = []
    failed = 0

    for consumer_cfg in consumers:
        consumer = consumer_cfg["repo"]

        if not validate_consumer(consumer):
            fail(f"Invalid consumer name: '{consumer}' (must match owner/repo)")
            results.append((consumer, "FAIL (invalid name)"))
            failed += 1
            continue

        log(f"--- Testing {consumer} ---")

        # Per-consumer overrides.
        c_strategy: str = consumer_cfg.get("strategy", "") or default_strategy
        c_branch: str = consumer_cfg.get("branch", "") or ""
        c_path: str = consumer_cfg.get("path", "") or ""

        if c_path:
            repo_dir: Optional[Path] = Path(c_path)
        else:
            repo_dir = resolve_repo(
                consumer,
                repos_root,
                c_strategy,
                c_branch,
                branch_pattern,
                dry_run=dry_run,
            )

        if repo_dir is None:
            results.append((consumer, "FAIL (not found)"))
            failed += 1
            continue

        log(f"  Using: {repo_dir}")

        _, status = run_consumer(
            consumer,
            repo_dir,
            wheel_path,
            setup_only=setup_only,
            force_fallback=force_fallback,
            with_docker=with_docker,
            dry_run=dry_run,
        )
        results.append((consumer, status))
        if "FAIL" in status:
            failed += 1

    print()
    log(f"=== {platform_label} Results ===")
    for name, status in results:
        print(f"  {name}: {status}")
    print()

    if failed > 0:
        fail(f"{platform_label}: {failed} consumer(s) FAILED")
    else:
        ok(f"{platform_label}: All consumers passed!")

    return failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments.

    ``--platform`` is required and must be ``linux`` or ``windows``.  It is
    always set by ``wrapper.py``; end users interact with the wrapper, not
    this script directly.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed :class:`argparse.Namespace`.
    """
    parser = argparse.ArgumentParser(
        prog="downstream.py",
        description="Validate nanvix-zutil against downstream consumers.",
    )
    parser.add_argument(
        "--platform",
        choices=["linux", "windows"],
        required=True,
        help="Platform to run on (set by wrapper.py, not by the end user).",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        default=str(_SCRIPT_DIR / "downstream.json"),
        help="Path to downstream.json (default: <script_dir>/downstream.json).",
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        default=False,
        help="Only run the setup phase.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        default=False,
        help="Skip wheel build; reuse an existing wheel.",
    )
    parser.add_argument(
        "--force-fallback",
        action="store_true",
        default=False,
        help="Force dependency-version fallback (implies --setup-only).",
    )
    parser.add_argument(
        "--with-docker",
        action="store_true",
        default=False,
        help="Pass --with-docker to nanvix-zutil build/test commands.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would happen without executing.",
    )
    parser.add_argument(
        "consumers",
        nargs="*",
        metavar="consumer",
        help="Owner/repo names to test (default: all from config).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for downstream.py.

    Parses arguments, ensures / loads config, builds the wheel once, then
    dispatches to :func:`run_platform` for the requested platform.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code: number of failed consumers (0 = all passed).
    """
    args = parse_args(argv)

    # --force-fallback implies --setup-only.
    if args.force_fallback:
        args.setup_only = True

    config_path = Path(args.config)
    cache_path = _SCRIPT_DIR / "consumer-repos.json"

    # Ensure config exists (auto-generate on first run).
    try:
        effective_config_path = ensure_config(
            config_path, cache_path, dry_run=args.dry_run
        )
    except RuntimeError as exc:
        fail(str(exc))
        return 1

    config = load_config(effective_config_path)

    # Filter consumers if specified on the CLI.
    all_consumers: list[dict] = config.get("consumers", [])
    if args.consumers:
        consumer_set = set(args.consumers)
        consumers = [c for c in all_consumers if c.get("repo") in consumer_set]
        # Also include any CLI-specified consumers not in config (they'll fail
        # validation downstream, giving a clear error message).
        config_repos = {c.get("repo") for c in all_consumers}
        for name in args.consumers:
            if name not in config_repos:
                consumers.append({"repo": name})
    else:
        consumers = all_consumers

    # Build wheel (once, shared across all consumers).
    zutils_root = Path.cwd()
    work_dir = Path(tempfile.gettempdir()) / "nanvix-downstream-test"

    wheel_path = build_wheel(
        zutils_root,
        work_dir,
        skip_build=args.skip_build,
        dry_run=args.dry_run,
    )

    failure_count = run_platform(
        consumers,
        config,
        wheel_path,
        platform=args.platform,
        setup_only=args.setup_only,
        force_fallback=args.force_fallback,
        with_docker=args.with_docker,
        dry_run=args.dry_run,
        skip_build=args.skip_build,
    )

    print()
    if failure_count > 0:
        fail(f"Overall: {failure_count} failure(s)")
    else:
        ok("Overall: All consumers passed!")

    return failure_count


if __name__ == "__main__":
    sys.exit(main())
