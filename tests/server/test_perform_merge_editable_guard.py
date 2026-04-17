"""Integration tests verifying perform_merge() aborts before cleanup on poisoned installs."""

from pathlib import Path

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core.types import (
    MergeFailedStep,
    MergeState,
    SubprocessResult,
    TerminationReason,
    TestResult,
)
from tests.fakes import InMemoryTestRunner, MockSubprocessRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SubprocessResult:
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )


@pytest.mark.anyio
async def test_perform_merge_aborts_before_cleanup_on_poisoned_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    When scan_editable_installs_for_worktree returns non-empty results after the merge
    step, perform_merge must return an error result WITHOUT calling git worktree remove.
    """
    import autoskillit.server.git as git_module
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    poisoned_report = ["autoskillit editable at file:///fake/worktree/src (autoskillit-0.6.12)"]

    monkeypatch.setattr(
        git_module,
        "scan_editable_installs_for_worktree",
        lambda worktree_path, site_packages_dirs=None: poisoned_report,
    )

    runner = MockSubprocessRunner()
    tester = InMemoryTestRunner(
        results=[TestResult(True, "= 10 passed =", ""), TestResult(True, "= 10 passed =", "")]
    )
    # Queue git calls in step order through merge (step 8); cleanup is never reached.
    runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt"))  # rev-parse (step 2)
    runner.push(_make_result(0, "feature-branch\n"))  # branch (step 3)
    runner.push(_make_result(0, ""))  # git ls-files (step 3c — no tracked generated files)
    runner.push(_make_result(0, ""))  # git status --porcelain (step 3d — clean)
    runner.push(_make_result(0, ""))  # git fetch (step 5)
    runner.push(_make_result(0, ""))  # ref check (step 5.5)
    runner.push(_make_result(0, ""))  # git log --merges (step 5.6 — no merge commits)
    runner.push(_make_result(0, ""))  # git rebase (step 6)
    runner.push(_make_result(0, f"worktree {fake_wt}\n"))  # git worktree list (step 7)
    runner.push(_make_result(0, "dev\n"))  # git branch --show-current (step 7.5)
    runner.push(_make_result(0, ""))  # git merge (step 8)
    # Step 8.5: editable guard fires (mocked above) — cleanup steps never reached

    result = await perform_merge(
        fake_wt, "dev", config=AutomationConfig(), runner=runner, tester=tester
    )

    assert result["merge_succeeded"] is True
    assert result["state"] == MergeState.MERGE_SUCCEEDED_CLEANUP_BLOCKED
    assert result["failed_step"] == MergeFailedStep.EDITABLE_INSTALL_GUARD
    assert "editable" in result["error"].lower()
    assert result["poisoned_installs"] == poisoned_report
    assert result["worktree_removed"] is False
    assert result["branch_deleted"] is False

    # Verify git worktree remove was NOT called (cleanup was blocked)
    wt_remove_calls = [
        args[0]
        for args in runner.call_args_list
        if len(args[0]) >= 3 and args[0][1] == "worktree" and args[0][2] == "remove"
    ]
    assert wt_remove_calls == [], "git worktree remove must not be called when guard blocks"


@pytest.mark.anyio
async def test_perform_merge_proceeds_normally_when_guard_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    When scan_editable_installs_for_worktree returns [] (no poisoned installs),
    perform_merge must NOT abort — it must proceed to cleanup normally.
    """
    import autoskillit.server.git as git_module
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)

    monkeypatch.setattr(
        git_module,
        "scan_editable_installs_for_worktree",
        lambda worktree_path, site_packages_dirs=None: [],
    )

    runner = MockSubprocessRunner()
    tester = InMemoryTestRunner(
        results=[TestResult(True, "= 10 passed =", ""), TestResult(True, "= 10 passed =", "")]
    )
    runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt"))  # rev-parse (step 2)
    runner.push(_make_result(0, "feature-branch\n"))  # branch (step 3)
    runner.push(_make_result(0, ""))  # git ls-files (step 3c)
    runner.push(_make_result(0, ""))  # git status --porcelain (step 3d)
    runner.push(_make_result(0, ""))  # git fetch (step 5)
    runner.push(_make_result(0, ""))  # ref check (step 5.5)
    runner.push(_make_result(0, ""))  # git log --merges (step 5.6)
    runner.push(_make_result(0, ""))  # git rebase (step 6)
    runner.push(_make_result(0, f"worktree {fake_wt}\n"))  # git worktree list (step 7)
    runner.push(_make_result(0, "dev\n"))  # git branch --show-current (step 7.5)
    runner.push(_make_result(0, ""))  # git merge (step 8)
    # Step 8.5: guard returns [] — cleanup proceeds
    # Steps 9-10 (wt remove, branch -D) use MockSubprocessRunner default (rc=0, stdout="")

    result = await perform_merge(
        fake_wt, "dev", config=AutomationConfig(), runner=runner, tester=tester
    )

    assert result.get("merge_succeeded") is True
    assert "error" not in result
    assert result["merged_branch"] == "feature-branch"
