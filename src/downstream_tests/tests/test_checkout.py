"""test_checkout.py — Tests for downstream_tests.checkout."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from downstream_tests.checkout import detect_strategy, resolve_branch, resolve_repo


# ---------------------------------------------------------------------------
# detect_strategy
# ---------------------------------------------------------------------------


def test_detect_nonexistent_path(tmp_path: Path):
    """Non-existent path → 'shallow'."""
    assert detect_strategy(tmp_path / "does-not-exist") == "shallow"


def test_detect_bare_repo(tmp_path: Path):
    """Directory with HEAD file but no .git → 'bare'."""
    repo = tmp_path / "repo.git"
    repo.mkdir()
    (repo / "HEAD").write_text("ref: refs/heads/main\n")
    # No .git entry at all
    assert detect_strategy(repo) == "bare"


def test_detect_bare_worktree(tmp_path: Path):
    """Directory with .git as a regular file → 'bare'."""
    repo = tmp_path / "wt"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /some/bare/.git\n")
    assert detect_strategy(repo) == "bare"


def test_detect_clone(tmp_path: Path):
    """Directory with .git/ as a directory → 'clone'."""
    repo = tmp_path / "clone"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert detect_strategy(repo) == "clone"


def test_detect_unknown(tmp_path: Path):
    """Empty directory with no recognisable git markers → 'shallow'."""
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
    """No pattern match → falls back to HEAD symref."""
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
        result = resolve_repo(
            "nanvix/zlib",
            repos_root,
            strategy="shallow",
            branch="nanvix/v1.0.0",
            dry_run=True,
        )
        mock_run.assert_not_called()

    assert result is not None
    assert "nanvix/zlib" in str(result)


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
