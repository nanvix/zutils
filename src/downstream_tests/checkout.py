"""checkout.py — Checkout helpers for downstream_tests."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from .log import dry, fail, log, ok


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
