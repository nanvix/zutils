"""checkout.py -- Checkout helpers for downstream_tests."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from .log import dry, fail, log, ok, warn

# Timeout in seconds for git subprocess calls.
_GIT_TIMEOUT = 600


def _is_dirty(work_dir: Path) -> bool:
    """Return True if the working tree has uncommitted changes.

    Checks both staged and unstaged modifications, plus untracked files
    that would be lost by a hard reset.

    Args:
        work_dir: Path to a git working directory (clone or worktree).

    Returns:
        True if there are uncommitted changes, False if clean.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(work_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        return bool(r.stdout.strip())
    except Exception:
        # If we can't determine status, assume dirty to be safe.
        return True


def _force_reset(
    consumer: str,
    work_dir: Path,
    target: str,
) -> None:
    """Reset to *target* unconditionally.

    Caller is responsible for checking dirty state beforehand if needed.

    Args:
        consumer:  Consumer slug (for log messages).
        work_dir:  Path to the git working directory.
        target:    Reset target, e.g. ``"origin/nanvix/v1.0.0"``.
    """
    subprocess.run(
        ["git", "-C", str(work_dir), "reset", "--hard", target],
        check=False,
        timeout=_GIT_TIMEOUT,
    )


def detect_strategy(repo_path: Path) -> str:
    """Auto-detect the checkout strategy for an existing repo path.

    Logic (mirrors bash detect_checkout_strategy):
    - Non-existent path -> "shallow"
    - Has HEAD file but no .git directory -> "bare"
    - .git is a regular file (gitdir pointer / worktree) -> "bare"
    - .git is a directory -> "clone"
    - Anything else -> "shallow"

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
        # .git is a file -> gitdir pointer -> worktree of a bare repo
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
            timeout=_GIT_TIMEOUT,
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
                timeout=_GIT_TIMEOUT,
            )
            symref = sym.stdout.strip()
            if symref.startswith("refs/heads/"):
                target_ref = symref.removeprefix("refs/heads/")
    else:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", remote_url, pattern],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
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
                        int(x) if x.isdigit() else x for x in re.split(r"[\./]", b)
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
                timeout=_GIT_TIMEOUT,
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
            ["git", "clone", "--bare", clone_url, str(repo_path)],
            timeout=_GIT_TIMEOUT,
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
            ],
            timeout=_GIT_TIMEOUT,
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
        timeout=_GIT_TIMEOUT,
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
            ],
            timeout=_GIT_TIMEOUT,
        )

    log(f"  {consumer}: fetching latest")
    subprocess.run(
        ["git", "-C", str(repo_path), "fetch", "origin", "--prune"],
        check=False,
        timeout=_GIT_TIMEOUT,
    )

    # Find existing worktrees via git worktree list.
    wt_result = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    worktrees: list[Path] = []
    for line in wt_result.stdout.splitlines():
        if line.startswith("worktree "):
            wt_path = Path(line.removeprefix("worktree ").strip())
            # Skip the bare repo itself (it shows up as a worktree entry).
            if wt_path != repo_path:
                worktrees.append(wt_path)

    if worktrees:
        # Prefer the last worktree (sorted by path for determinism).
        wt_dir = sorted(worktrees, key=lambda p: str(p))[-1]
        # Snapshot dirty state *before* we touch anything.  If the tree
        # was already dirty (e.g. user edits), warn and skip reset.  If
        # it was clean, any new untracked files (like .nanvix/ build
        # artifacts from a prior run) won't block us.
        was_dirty = _is_dirty(wt_dir)
        log(f"  {consumer}: updating worktree at {wt_dir}")
        subprocess.run(
            ["git", "-C", str(wt_dir), "fetch", "origin"],
            check=False,
            timeout=_GIT_TIMEOUT,
        )
        # Reset to the *resolved* branch, not whatever HEAD currently
        # points to -- the old HEAD branch may have been deleted upstream
        # (fetch --prune removes the remote-tracking ref).
        if was_dirty:
            warn(f"  {consumer}: skipping reset -- uncommitted changes in {wt_dir}")
        else:
            _force_reset(consumer, wt_dir, f"origin/{branch}")
        return wt_dir

    wt_dir = repo_path / branch

    if wt_dir.is_dir():
        was_dirty = _is_dirty(wt_dir)
        log(f"  {consumer}: updating worktree at {wt_dir}")
        subprocess.run(
            ["git", "-C", str(wt_dir), "fetch", "origin"],
            check=False,
            timeout=_GIT_TIMEOUT,
        )
        if was_dirty:
            warn(f"  {consumer}: skipping reset -- uncommitted changes in {wt_dir}")
        else:
            _force_reset(consumer, wt_dir, f"origin/{branch}")
        return wt_dir

    log(f"  {consumer}: creating worktree for {branch}")
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", str(wt_dir), branch],
        timeout=_GIT_TIMEOUT,
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
        result = subprocess.run(
            ["git", "clone", clone_url, str(repo_path)], timeout=_GIT_TIMEOUT
        )
        if result.returncode != 0:
            fail(f"  {consumer}: git clone failed")
            return None
        ok(f"  {consumer}: cloned")

    was_dirty = _is_dirty(repo_path)
    log(f"  {consumer}: fetching and checking out {branch}")
    subprocess.run(
        ["git", "-C", str(repo_path), "fetch", "origin"],
        check=False,
        timeout=_GIT_TIMEOUT,
    )
    co = subprocess.run(
        ["git", "-C", str(repo_path), "checkout", branch],
        capture_output=True,
        timeout=_GIT_TIMEOUT,
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
            ],
            timeout=_GIT_TIMEOUT,
        )
    if was_dirty:
        warn(f"  {consumer}: skipping reset -- uncommitted changes in {repo_path}")
    else:
        _force_reset(consumer, repo_path, f"origin/{branch}")
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
            ["git", "clone", "--depth", "1", "-b", branch, clone_url, str(repo_path)],
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            fail(f"  {consumer}: shallow clone failed")
            return None
        ok(f"  {consumer}: cloned (shallow)")
        return repo_path

    log(f"  {consumer}: updating shallow clone")
    was_dirty = _is_dirty(repo_path)
    subprocess.run(
        ["git", "-C", str(repo_path), "fetch", "--depth", "1", "origin", branch],
        check=False,
        timeout=_GIT_TIMEOUT,
    )
    if was_dirty:
        warn(f"  {consumer}: skipping reset -- uncommitted changes in {repo_path}")
    else:
        _force_reset(consumer, repo_path, f"origin/{branch}")
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

    if not strategy or strategy == "auto":
        strategy = detect_strategy(repo_path)
        log(f"  {consumer}: auto-detected strategy: {strategy}")
    elif repo_path.exists():
        # Override config default with auto-detection when the repo
        # already exists locally -- prevents e.g. shallow-cloning over
        # an existing bare+worktree checkout.
        detected = detect_strategy(repo_path)
        if detected != strategy:
            log(
                f"  {consumer}: overriding strategy '{strategy}' -> '{detected}' (repo exists)"
            )
            strategy = detected

    if not branch:
        branch = resolve_branch(consumer, repo_path, strategy, pattern) or ""
        if not branch and dry_run:
            # Fallback placeholder only if resolution fails (e.g. no network)
            branch = pattern.replace("*", "X.Y.Z")

    if not branch:
        fail(f"  {consumer}: cannot determine target branch")
        return None

    log(f"  {consumer}: strategy={strategy} branch={branch}")

    if dry_run:
        target_dir = (repo_path / branch) if strategy == "bare" else repo_path
        dry(f"  {consumer}: would resolve via '{strategy}' strategy")
        if not repo_path.exists():
            dry(
                f"  {consumer}: would clone https://github.com/{consumer}.git -> {repo_path}"
            )
        else:
            dry(f"  {consumer}: would fetch + update at {repo_path}")
        dry(f"  {consumer}: working directory -> {target_dir}")
        return target_dir

    if strategy == "bare":
        return _resolve_bare(consumer, repo_path, branch)
    if strategy == "clone":
        return _resolve_clone(consumer, repo_path, branch)
    if strategy == "shallow":
        return _resolve_shallow(consumer, repo_path, branch)

    fail(f"  {consumer}: unknown checkout strategy: {strategy}")
    return None
