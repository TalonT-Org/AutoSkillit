"""Tests for LoC capture helpers in execution.headless.

T-GIT-1..T-GIT-4: unit tests for _parse_numstat and _compute_loc_changed.
T-GIT-5: real git repo + worktree baseline (validates primitive with real git).
T-GIT-6: PostSessionMetrics resolves effective_cwd from worktree_path.
T-GIT-7: LoC is non-zero when worktree has commits.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _parse_numstat(numstat_output: str):
    from autoskillit.execution.headless import _parse_numstat

    return _parse_numstat(numstat_output)


def _compute_loc_changed(cwd: str, pre_sha: str):
    from autoskillit.execution.headless import _compute_loc_changed

    return _compute_loc_changed(cwd, pre_sha)


def _capture_git_head_sha(cwd: str):
    from autoskillit.execution.headless import _capture_git_head_sha

    return _capture_git_head_sha(cwd)


def _compute_post_session_metrics(cwd, pre_session_sha, skill_result):
    from autoskillit.execution.headless import _compute_post_session_metrics

    return _compute_post_session_metrics(cwd, pre_session_sha, skill_result)


# T-GIT-1
def test_compute_loc_changed_parses_numstat_output():
    """_parse_numstat correctly sums numstat lines: insertions, deletions."""
    numstat_output = "10\t5\tfile.py\n3\t1\tsrc/foo.py\n"
    result = _parse_numstat(numstat_output)
    assert result == (13, 6)


# T-GIT-2
def test_compute_loc_changed_returns_zero_on_subprocess_error(tmp_path: Path):
    """When git subprocess fails (non-git dir), loc capture returns (0, 0)."""
    result = _compute_loc_changed(str(tmp_path), "abc1234")
    assert result == (0, 0)


# T-GIT-3
def test_compute_loc_changed_handles_empty_diff():
    """Empty numstat output (no changes) → (0, 0)."""
    assert _parse_numstat("") == (0, 0)


# T-GIT-4
def test_compute_loc_changed_handles_binary_files():
    """Binary file lines (-\t-\tbinary.png) are ignored without error."""
    numstat_output = "-\t-\timage.png\n5\t2\tfile.py\n"
    assert _parse_numstat(numstat_output) == (5, 2)


def test_capture_git_head_sha_returns_empty_on_non_git_dir(tmp_path: Path):
    """_capture_git_head_sha returns '' when directory is not a git repo."""
    result = _capture_git_head_sha(str(tmp_path))
    assert result == ""


def test_capture_git_head_sha_returns_empty_string_on_exception():
    """_capture_git_head_sha returns '' on any subprocess exception."""
    with patch("subprocess.run", side_effect=OSError("mocked error")):
        result = _capture_git_head_sha("/any/path")
    assert result == ""


def test_compute_loc_changed_returns_zero_for_empty_pre_sha(tmp_path: Path):
    """_compute_loc_changed returns (0, 0) when pre_sha is empty."""
    result = _compute_loc_changed(str(tmp_path), "")
    assert result == (0, 0)


def _setup_git_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    (repo_path / "init.txt").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )


# T-GIT-5
def test_compute_loc_changed_clone_root_vs_worktree(tmp_path: Path):
    """Clone root returns (0,0); worktree with new commits returns non-zero LoC."""
    repo_path = tmp_path / "repo"
    _setup_git_repo(repo_path)

    pre_sha = _capture_git_head_sha(str(repo_path))
    assert pre_sha, "expected non-empty SHA from real git repo"

    worktree_path = tmp_path / "worktree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch", str(worktree_path)],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
    )
    (worktree_path / "feature.txt").write_text("new content\n" * 5)
    subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add feature"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
    )

    # Clone root is untouched — LoC should be (0, 0)
    assert _compute_loc_changed(str(repo_path), pre_sha) == (0, 0)

    # Worktree has new commits — LoC should be non-zero insertions, zero deletions
    wt_ins, wt_del = _compute_loc_changed(str(worktree_path), pre_sha)
    assert wt_ins > 0
    assert wt_del == 0


# T-GIT-6
def test_post_session_metrics_effective_cwd_resolution(tmp_path: Path):
    """PostSessionMetrics resolves effective_cwd from worktree_path when set."""
    cwd = str(tmp_path / "clone")
    worktree = str(tmp_path / "worktree")

    mock_result_no_worktree = MagicMock()
    mock_result_no_worktree.worktree_path = None

    mock_result_with_worktree = MagicMock()
    mock_result_with_worktree.worktree_path = worktree

    with patch("autoskillit.execution.headless._compute_loc_changed", return_value=(0, 0)):
        metrics_no_wt = _compute_post_session_metrics(cwd, "abc123", mock_result_no_worktree)
        metrics_with_wt = _compute_post_session_metrics(cwd, "abc123", mock_result_with_worktree)

    assert metrics_no_wt.effective_cwd == cwd
    assert metrics_with_wt.effective_cwd == worktree


# T-GIT-7
def test_post_session_metrics_non_zero_loc_for_worktree(tmp_path: Path):
    """PostSessionMetrics returns non-zero LoC when worktree has new commits."""
    repo_path = tmp_path / "repo"
    _setup_git_repo(repo_path)

    pre_sha = _capture_git_head_sha(str(repo_path))
    assert pre_sha

    worktree_path = tmp_path / "worktree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch2", str(worktree_path)],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
    )
    (worktree_path / "impl.py").write_text("x = 1\n" * 10)
    subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "implement"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
    )

    mock_skill_result = MagicMock()
    mock_skill_result.worktree_path = str(worktree_path)

    metrics = _compute_post_session_metrics(str(repo_path), pre_sha, mock_skill_result)

    assert metrics.loc_insertions > 0
    assert metrics.effective_cwd == str(worktree_path)
