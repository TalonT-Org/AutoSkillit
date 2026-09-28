"""Tests for evidence-scoped LoC capture helpers in execution.headless."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import EvidenceWorktree, EvidenceWorktreeSource
from autoskillit.execution.headless._headless_git import (
    SessionGitEvidence,
    _capture_pre_session_git_state,
    _compute_loc_changed,
    _observe_session_git_evidence,
    _parse_numstat,
)
from autoskillit.execution.headless._headless_helpers import _compute_post_session_metrics
from tests._git_topology import add_linked_worktree, commit_file, init_checkout

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _evidence(path: Path, baseline_sha: str) -> SessionGitEvidence:
    return SessionGitEvidence(
        worktree=EvidenceWorktree(
            path=str(path),
            source=EvidenceWorktreeSource.LAUNCH_CHECKOUT,
        ),
        git_writes_detected=False,
        loc_baseline_sha=baseline_sha,
        new_worktrees=(),
    )


def test_parse_numstat_sums_insertions_and_deletions() -> None:
    assert _parse_numstat("10\t5\tfile.py\n3\t1\tsrc/foo.py\n") == (13, 6)


def test_compute_loc_changed_returns_zero_on_subprocess_error(tmp_path: Path) -> None:
    assert _compute_loc_changed(_evidence(tmp_path, "abc1234")) == (0, 0)


def test_compute_loc_changed_returns_zero_for_empty_baseline(tmp_path: Path) -> None:
    assert _compute_loc_changed(_evidence(tmp_path, "")) == (0, 0)


@pytest.mark.parametrize(
    "numstat,expected",
    [
        ("", (0, 0)),
        ("-\t-\timage.png\n5\t2\tfile.py\n", (5, 2)),
    ],
)
def test_parse_numstat_ignores_empty_and_binary_rows(
    numstat: str, expected: tuple[int, int]
) -> None:
    assert _parse_numstat(numstat) == expected


def test_post_session_metrics_measure_new_worktree_from_evidence(tmp_path: Path) -> None:
    repo = init_checkout(tmp_path / "clone")
    pre = _capture_pre_session_git_state(str(repo))
    worktree = add_linked_worktree(repo, "feature")
    commit_file(worktree, "feature.py", "new content\n" * 5, "add feature")
    evidence = _observe_session_git_evidence(pre, [f"worktree_path = {worktree}"])

    metrics = _compute_post_session_metrics(evidence)

    assert metrics.loc_insertions > 0
    assert metrics.loc_deletions == 0
