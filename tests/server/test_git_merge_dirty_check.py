"""Tests for the pre-merge dirty check in perform_merge (Layer 3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import (
    MergeFailedStep,
    MergeState,
    SubprocessResult,
    TerminationReason,
    TestResult,
)
from tests.fakes import InMemoryTestRunner, MockSubprocessRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> SubprocessResult:
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )


def _make_tester() -> InMemoryTestRunner:
    return InMemoryTestRunner(
        results=[
            TestResult(True, "PASS\n= 10 passed =", ""),
            TestResult(True, "PASS\n= 10 passed =", ""),
        ]
    )


def _push_through_verify_merge(
    runner: MockSubprocessRunner,
    fake_wt: str,
    *,
    base_branch: str = "dev",
) -> None:
    """Push responses through step 7.5 (_verify_merge_target)."""
    runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt"))  # rev-parse --git-dir
    runner.push(_make_result(0, "feature-branch\n"))  # branch --show-current (worktree)
    runner.push(_make_result(0, ""))  # git ls-files (generated)
    runner.push(_make_result(0, ""))  # git status --porcelain (worktree clean)
    runner.push(_make_result(0, ""))  # fetch
    runner.push(_make_result(0, "abc123\n"))  # rev-parse --verify
    runner.push(_make_result(0, ""))  # git log --merges
    runner.push(_make_result(0, ""))  # rebase
    runner.push(
        _make_result(0, f"worktree /repo\nHEAD abc123\nbranch refs/heads/{base_branch}\n\n")
    )  # wt list
    runner.push(_make_result(0, f"{base_branch}\n"))  # step 7.5: branch --show-current (main_repo)


@pytest.mark.anyio
async def test_dirty_main_repo_returns_error(tmp_path):
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path / "worktrees" / "impl-test")
    Path(fake_wt).mkdir(parents=True)
    (Path(fake_wt) / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

    runner = MockSubprocessRunner()
    _push_through_verify_merge(runner, fake_wt)
    runner.push(_make_result(0, " M src/foo.py\n M tests/bar.py\n"))  # step 7.6: dirty!

    with patch("autoskillit.server.git.scan_editable_installs_for_worktree", return_value=[]):
        result = await perform_merge(
            fake_wt,
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=_make_tester(),
        )

    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.DIRTY_MAIN_REPO
    assert result["state"] == MergeState.WORKTREE_INTACT
    assert result["worktree_path"] == fake_wt
    assert len(result["dirty_files"]) == 2


@pytest.mark.anyio
async def test_clean_main_repo_proceeds_to_merge(tmp_path):
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path / "worktrees" / "impl-test")
    Path(fake_wt).mkdir(parents=True)
    (Path(fake_wt) / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

    runner = MockSubprocessRunner()
    _push_through_verify_merge(runner, fake_wt)
    runner.push(_make_result(0, ""))  # step 7.6: clean
    runner.push(_make_result(0, ""))  # merge
    runner.push(_make_result(0, ""))  # wt remove
    runner.push(_make_result(0, ""))  # branch -D

    with patch("autoskillit.server.git.scan_editable_installs_for_worktree", return_value=[]):
        result = await perform_merge(
            fake_wt,
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=_make_tester(),
        )

    assert result.get("merge_succeeded") is True


@pytest.mark.anyio
async def test_dirty_check_error_format(tmp_path):
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path / "worktrees" / "impl-test")
    Path(fake_wt).mkdir(parents=True)
    (Path(fake_wt) / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

    runner = MockSubprocessRunner()
    _push_through_verify_merge(runner, fake_wt)
    dirty_output = " M file1.py\n M file2.py\n?? newfile.txt\n"
    runner.push(_make_result(0, dirty_output))  # step 7.6: dirty

    with patch("autoskillit.server.git.scan_editable_installs_for_worktree", return_value=[]):
        result = await perform_merge(
            fake_wt,
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=_make_tester(),
        )

    assert "3 dirty file(s)" in result["error"]
    assert "Clean the working tree" in result["error"]
    assert len(result["dirty_files"]) == 3
