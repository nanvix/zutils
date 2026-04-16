"""test_checkout.py -- Tests for downstream_tests.checkout."""

# pyright: reportPrivateUsage=false

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from downstream_tests.checkout import detect_strategy, resolve_branch, resolve_repo


# ---------------------------------------------------------------------------
# detect_strategy
# ---------------------------------------------------------------------------


def test_detect_nonexistent_path(tmp_path: Path):
    """Non-existent path -> 'shallow'."""
    assert detect_strategy(tmp_path / "does-not-exist") == "shallow"


def test_detect_bare_repo(tmp_path: Path):
    """Directory with HEAD file but no .git -> 'bare'."""
    repo = tmp_path / "repo.git"
    repo.mkdir()
    (repo / "HEAD").write_text("ref: refs/heads/main\n")
    # No .git entry at all
    assert detect_strategy(repo) == "bare"


def test_detect_bare_worktree(tmp_path: Path):
    """Directory with .git as a regular file -> 'bare'."""
    repo = tmp_path / "wt"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /some/bare/.git\n")
    assert detect_strategy(repo) == "bare"


def test_detect_clone(tmp_path: Path):
    """Directory with .git/ as a directory -> 'clone'."""
    repo = tmp_path / "clone"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert detect_strategy(repo) == "clone"


def test_detect_unknown(tmp_path: Path):
    """Empty directory with no recognisable git markers -> 'shallow'."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert detect_strategy(empty) == "shallow"


# ---------------------------------------------------------------------------
# resolve_branch
# ---------------------------------------------------------------------------


def _make_run_result(stdout: str, returncode: int = 0):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = returncode
    return r


def test_resolve_branch_bare_local_refs(tmp_path: Path):
    """Bare strategy with existing dir: uses git for-each-ref output."""
    repo_path = tmp_path / "nanvix" / "zlib"
    repo_path.mkdir(parents=True)

    side_effects = [
        _make_run_result("origin/nanvix/v1.0.0\n"),  # for-each-ref
    ]

    with patch("subprocess.run", side_effect=side_effects):
        branch = resolve_branch("nanvix/zlib", repo_path, "bare", "nanvix/v*")

    assert branch == "nanvix/v1.0.0"


def test_resolve_branch_clone_ls_remote(tmp_path: Path):
    """Clone/shallow strategy: uses git ls-remote --heads."""
    repo_path = tmp_path / "nanvix" / "zlib"
    # Do NOT create this dir so strategy != bare existing

    ls_remote_output = (
        "abc123\trefs/heads/nanvix/v1.0.0\n"
        "def456\trefs/heads/nanvix/v2.0.0\n"
    )
    side_effects = [
        _make_run_result(ls_remote_output),  # ls-remote --heads
    ]

    with patch("subprocess.run", side_effect=side_effects):
        branch = resolve_branch("nanvix/zlib", repo_path, "clone", "nanvix/v*")

    assert branch == "nanvix/v2.0.0"


def test_resolve_branch_fallback_to_default(tmp_path: Path):
    """No pattern match -> falls back to HEAD symref."""
    repo_path = tmp_path / "nanvix" / "zlib"

    # ls-remote --heads returns nothing matching
    ls_heads = _make_run_result("")
    # ls-remote --symref HEAD returns the default branch
    ls_symref = _make_run_result("ref: refs/heads/main\tHEAD\nabc123\tHEAD\n")

    with patch("subprocess.run", side_effect=[ls_heads, ls_symref]):
        branch = resolve_branch("nanvix/zlib", repo_path, "shallow", "nanvix/v*")

    assert branch == "main"


# ---------------------------------------------------------------------------
# resolve_repo
# ---------------------------------------------------------------------------


def test_resolve_repo_dry_run(tmp_path: Path):
    """dry_run=True returns expected path without any git calls."""
    repos_root = tmp_path / "repos"
    repos_root.mkdir()

    with patch("subprocess.run") as mock_run:
        _result = resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="shallow",
            branch="nanvix/v1.0.0",
            dry_run=True,
        )
        mock_run.assert_not_called()


def test_resolve_repo_dry_run_placeholder_no_double_v(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When branch resolution fails in dry-run, the fallback placeholder
    from pattern 'nanvix/v*' should be 'nanvix/vX.Y.Z', not 'nanvix/vvX.Y.Z'.

    Regression: pattern.replace("*", "vX.Y.Z") doubled the 'v' because
    the pattern already contains it.
    """
    repos_root = tmp_path / "repos"
    repos_root.mkdir()

    # Simulate failed branch resolution (no network, no local refs)
    with patch("downstream_tests.checkout.resolve_branch", return_value=None):
        resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="shallow",
            pattern="nanvix/v*",
            dry_run=True,
        )

    output = capsys.readouterr().out
    assert "nanvix/vX.Y.Z" in output
    assert "nanvix/vvX.Y.Z" not in output


def test_resolve_repo_dry_run_resolves_real_branch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run should attempt real branch resolution before falling back."""
    repos_root = tmp_path / "repos"
    repos_root.mkdir()

    with patch(
        "downstream_tests.checkout.resolve_branch",
        return_value="nanvix/v3.2.1",
    ) as mock_resolve:
        _result = resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="shallow",
            pattern="nanvix/v*",
            dry_run=True,
        )

    mock_resolve.assert_called_once()
    output = capsys.readouterr().out
    assert "nanvix/v3.2.1" in output


def test_resolve_repo_dispatches_to_strategy(tmp_path: Path):
    """resolve_repo calls the correct _resolve_* helper based on strategy."""
    repos_root = tmp_path / "repos"
    repos_root.mkdir()

    fake_path = tmp_path / "result"

    with patch(
        "downstream_tests.checkout._resolve_shallow", return_value=fake_path
    ) as mock_shallow:
        result = resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="shallow",
            branch="nanvix/v1.0.0",
        )

    mock_shallow.assert_called_once()
    assert result == fake_path


# ---------------------------------------------------------------------------
# Regression: strategy override when repo already exists locally
# ---------------------------------------------------------------------------


def test_resolve_repo_overrides_shallow_for_existing_clone(tmp_path: Path):
    """Config says 'shallow' but repo exists as a clone -> auto-detect wins.

    Regression: previously, passing strategy='shallow' from the config default
    bypassed auto-detection entirely, causing shallow operations on existing
    bare/clone repos.
    """
    repos_root = tmp_path / "repos"
    repo_path = repos_root / "nanvix" / "zlib"
    repo_path.mkdir(parents=True)
    # Make it look like a normal clone (.git is a directory)
    (repo_path / ".git").mkdir()

    fake_path = tmp_path / "result"

    with patch(
        "downstream_tests.checkout._resolve_clone", return_value=fake_path
    ) as mock_clone:
        result = resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="shallow",
            branch="nanvix/v1.0.0",
        )

    # Should have dispatched to _resolve_clone, NOT _resolve_shallow
    mock_clone.assert_called_once()
    assert result == fake_path


def test_resolve_repo_overrides_shallow_for_existing_bare_worktree(tmp_path: Path):
    """Config says 'shallow' but repo is a bare worktree (.git file) -> 'bare'.

    Regression: repos checked out as bare+worktree were being treated as
    shallow because the config default was passed through without detection.
    """
    repos_root = tmp_path / "repos"
    repo_path = repos_root / "nanvix" / "zlib"
    repo_path.mkdir(parents=True)
    # .git as a file -> worktree of a bare repo
    (repo_path / ".git").write_text("gitdir: /some/bare/.git\n")

    fake_path = tmp_path / "result"

    with patch(
        "downstream_tests.checkout._resolve_bare", return_value=fake_path
    ) as mock_bare:
        result = resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="shallow",
            branch="nanvix/v1.0.0",
        )

    mock_bare.assert_called_once()
    assert result == fake_path


def test_resolve_repo_auto_strategy_explicit(tmp_path: Path):
    """strategy='auto' triggers detection just like strategy=''."""
    repos_root = tmp_path / "repos"
    repo_path = repos_root / "nanvix" / "zlib"
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()

    fake_path = tmp_path / "result"

    with patch(
        "downstream_tests.checkout._resolve_clone", return_value=fake_path
    ) as mock_clone:
        result = resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="auto",
            branch="nanvix/v1.0.0",
        )

    mock_clone.assert_called_once()
    assert result == fake_path


def test_resolve_repo_no_override_when_strategy_matches(tmp_path: Path):
    """No override log when config strategy already matches detection."""
    repos_root = tmp_path / "repos"
    repo_path = repos_root / "nanvix" / "zlib"
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()  # clone

    fake_path = tmp_path / "result"

    with patch(
        "downstream_tests.checkout._resolve_clone", return_value=fake_path
    ) as mock_clone:
        result = resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="clone",
            branch="nanvix/v1.0.0",
        )

    mock_clone.assert_called_once()
    assert result == fake_path


# ---------------------------------------------------------------------------
# _is_dirty / _safe_reset
# ---------------------------------------------------------------------------

import subprocess as _subprocess


def test_is_dirty_clean_repo(tmp_path: Path):
    """_is_dirty returns False for a clean working tree."""
    from downstream_tests.checkout import _is_dirty

    repo = tmp_path / "repo"
    repo.mkdir()
    _subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True,
    )
    assert _is_dirty(repo) is False


def test_is_dirty_with_modifications(tmp_path: Path):
    """_is_dirty returns True when there are uncommitted changes."""
    from downstream_tests.checkout import _is_dirty

    repo = tmp_path / "repo"
    repo.mkdir()
    _subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True,
    )
    (repo / "dirty.txt").write_text("uncommitted work")
    assert _is_dirty(repo) is True


def test_is_dirty_with_staged_changes(tmp_path: Path):
    """_is_dirty returns True when there are staged but uncommitted changes."""
    from downstream_tests.checkout import _is_dirty

    repo = tmp_path / "repo"
    repo.mkdir()
    _subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True,
    )
    (repo / "staged.txt").write_text("staged content")
    _subprocess.run(
        ["git", "-C", str(repo), "add", "staged.txt"],
        check=True, capture_output=True,
    )
    assert _is_dirty(repo) is True


def test_safe_reset_skips_dirty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """_safe_reset refuses to reset a dirty working tree."""
    from downstream_tests.checkout import _safe_reset

    repo = tmp_path / "repo"
    repo.mkdir()
    _subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True,
    )
    (repo / "dirty.txt").write_text("do not lose me")

    result = _safe_reset("nanvix/test", repo, "HEAD")
    assert result is False

    output = capsys.readouterr().out
    assert "WARN" in output
    assert "uncommitted changes" in output


def test_safe_reset_proceeds_when_clean(tmp_path: Path):
    """_safe_reset resets a clean working tree."""
    from downstream_tests.checkout import _safe_reset

    repo = tmp_path / "repo"
    repo.mkdir()
    _subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True,
    )

    result = _safe_reset("nanvix/test", repo, "HEAD")
    assert result is True


# ---------------------------------------------------------------------------
# _resolve_bare integration (real git repos)
# ---------------------------------------------------------------------------


def _make_bare_with_worktree(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a bare repo with a worktree for testing.

    Returns (bare_path, worktree_path, branch_name).
    """
    bare = tmp_path / "bare.git"
    src = tmp_path / "src"
    src.mkdir()
    _subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
    _subprocess.run(
        ["git", "-C", str(src), "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True,
    )
    _subprocess.run(
        ["git", "-C", str(src), "branch", "nanvix/v1.0.0"],
        check=True, capture_output=True,
    )
    _subprocess.run(
        ["git", "clone", "--bare", str(src), str(bare)],
        check=True, capture_output=True,
    )
    _subprocess.run(
        ["git", "-C", str(bare), "config", "remote.origin.fetch",
         "+refs/heads/*:refs/remotes/origin/*"],
        check=True, capture_output=True,
    )
    wt = bare / "nanvix" / "v1.0.0"
    _subprocess.run(
        ["git", "-C", str(bare), "worktree", "add", str(wt), "nanvix/v1.0.0"],
        check=True, capture_output=True,
    )
    return bare, wt, "nanvix/v1.0.0"


def test_resolve_bare_skips_dirty_worktree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """_resolve_bare does not reset a worktree with uncommitted changes."""
    from downstream_tests.checkout import _resolve_bare

    bare, wt, branch = _make_bare_with_worktree(tmp_path)
    (wt / "uncommitted.txt").write_text("precious work")

    result = _resolve_bare("nanvix/test", bare, branch)
    assert result is not None

    # The file should still be there (not clobbered)
    assert (wt / "uncommitted.txt").exists()
    assert (wt / "uncommitted.txt").read_text() == "precious work"

    output = capsys.readouterr().out
    assert "uncommitted changes" in output


def test_resolve_bare_resets_clean_worktree(tmp_path: Path):
    """_resolve_bare resets a clean worktree without issues."""
    from downstream_tests.checkout import _resolve_bare

    bare, wt, branch = _make_bare_with_worktree(tmp_path)

    result = _resolve_bare("nanvix/test", bare, branch)
    assert result is not None
    assert result == wt


def test_resolve_bare_uses_git_worktree_list(tmp_path: Path):
    """_resolve_bare finds worktrees via git worktree list, not hardcoded glob."""
    from downstream_tests.checkout import _resolve_bare

    bare, wt, branch = _make_bare_with_worktree(tmp_path)

    result = _resolve_bare("nanvix/test", bare, branch)
    assert result == wt
