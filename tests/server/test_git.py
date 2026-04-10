"""Tests for server/git.py perform_merge()."""

from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import CleanupResult
from autoskillit.core.types import (
    MergeFailedStep,
    MergeState,
    SubprocessResult,
    TerminationReason,
    TestResult,
)
from tests.conftest import MockSubprocessRunner, StatefulMockTester


def _make_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    termination_reason: TerminationReason = TerminationReason.NATURAL_EXIT,
) -> SubprocessResult:
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=termination_reason,
        pid=12345,
    )


@pytest.fixture
def default_config():
    return AutomationConfig()


@pytest.fixture
def conftest_mock_runner():
    from tests.conftest import MockSubprocessRunner

    return MockSubprocessRunner()


@pytest.mark.anyio
async def test_perform_merge_returns_error_for_nonexistent_path(
    default_config, conftest_mock_runner
):
    from autoskillit.server.git import perform_merge

    result = await perform_merge(
        "/nonexistent/path", "dev", config=default_config, runner=conftest_mock_runner
    )
    assert "error" in result


@pytest.mark.anyio
async def test_perform_merge_rejects_protected_branch(
    default_config, conftest_mock_runner, tmp_path
):
    """perform_merge must reject when base_branch is a protected branch."""
    from autoskillit.core.types import MergeFailedStep, MergeState
    from autoskillit.server.git import perform_merge

    wt = tmp_path / "wt"
    wt.mkdir()

    result = await perform_merge(
        str(wt), "main", config=default_config, runner=conftest_mock_runner
    )

    assert result["failed_step"] == MergeFailedStep.PROTECTED_BRANCH
    assert result["state"] == MergeState.WORKTREE_INTACT
    assert "protected" in result["error"].lower()
    assert len(conftest_mock_runner.call_args_list) == 0  # no git commands run


@pytest.mark.anyio
async def test_perform_merge_rejects_dirty_worktree(
    default_config, conftest_mock_runner, tmp_path
):
    """perform_merge() rejects worktrees with uncommitted changes."""
    from autoskillit.core.types import MergeFailedStep, MergeState
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (no tracked generated files)
    conftest_mock_runner.push(_make_result(0, " M hooks.json\n", ""))  # git status --porcelain

    result = await perform_merge(
        fake_wt, "dev", config=default_config, runner=conftest_mock_runner
    )
    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.DIRTY_TREE
    assert result["state"] == MergeState.WORKTREE_DIRTY
    assert "hooks.json" in str(result["dirty_files"])


@pytest.mark.anyio
async def test_perform_merge_dirty_tree_reports_files(
    default_config, conftest_mock_runner, tmp_path
):
    """perform_merge() lists all dirty files in the error response."""
    from autoskillit.core.types import MergeFailedStep
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (no tracked generated files)
    dirty_output = " M hooks.json\n M .claude/settings.json\n?? untracked.txt\n"
    conftest_mock_runner.push(_make_result(0, dirty_output, ""))  # git status --porcelain

    result = await perform_merge(
        fake_wt, "dev", config=default_config, runner=conftest_mock_runner
    )
    assert result["failed_step"] == MergeFailedStep.DIRTY_TREE
    assert len(result["dirty_files"]) == 3
    assert "3 dirty file(s)" in result["error"]


@pytest.mark.anyio
async def test_perform_merge_blocks_on_failing_tests(
    default_config, conftest_mock_runner, tmp_path
):
    from autoskillit.core.types import MergeFailedStep, MergeState
    from autoskillit.server.git import perform_merge

    # Create a temp dir to use as a fake worktree path so os.path.isdir passes
    fake_wt = str(tmp_path)
    # Queue: rev-parse (worktree ok), branch, dirty check, test (fails)
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(1, "= 1 failed =", ""))  # test

    result = await perform_merge(
        fake_wt, "dev", config=default_config, runner=conftest_mock_runner
    )
    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.TEST_GATE
    assert result["state"] == MergeState.WORKTREE_INTACT


@pytest.mark.anyio
async def test_perform_merge_returns_success_on_green_tests(
    default_config, conftest_mock_runner, tmp_path
):
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(
        results=[TestResult(True, "= 50 passed =", ""), TestResult(True, "= 50 passed =", "")]
    )
    # Queue 9 steps: rev-parse, branch, dirty check, fetch, rebase,
    # wt-list, merge, remove, branch-D
    # (test gate now handled by tester, not subprocess)
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check (5.5)
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges (5.6 — no merge commits)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)
    conftest_mock_runner.push(_make_result(0, f"worktree {fake_wt}\n", ""))  # wt list
    conftest_mock_runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt,
        "dev",
        config=default_config,
        runner=conftest_mock_runner,
        tester=tester,
    )
    assert result.get("merge_succeeded") is True
    assert result["merged_branch"] == "feature-branch"
    assert tester.call_count == 2  # both pre- and post-rebase gates ran


@pytest.mark.anyio
async def test_perform_merge_blocks_on_post_rebase_test_failure(
    default_config, conftest_mock_runner, tmp_path
):
    """Post-rebase test failure returns POST_REBASE_TEST_GATE and WORKTREE_INTACT."""
    from autoskillit.core.types import MergeFailedStep, MergeState
    from autoskillit.server.git import perform_merge

    # Pre-rebase: pass; post-rebase: fail
    tester = StatefulMockTester(
        results=[TestResult(True, "= 10 passed =", ""), TestResult(False, "= 1 failed =", "")]
    )
    # Queue: rev-parse (valid worktree), branch, dirty check, fetch ok,
    # ref check (5.5), git log --merges (5.6, no merge commits), rebase ok
    # No merge/cleanup queued — gate blocks before those
    conftest_mock_runner.push(_make_result(0, f"{str(tmp_path)}/.git/worktrees/feature", ""))
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch ok
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check (5.5)
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges (5.6 — no merge commits)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase ok
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)

    result = await perform_merge(
        str(tmp_path), "dev", config=default_config, runner=conftest_mock_runner, tester=tester
    )

    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.POST_REBASE_TEST_GATE
    assert result["state"] == MergeState.WORKTREE_INTACT
    assert "worktree_path" in result
    assert result["test_stdout"] == "= 1 failed ="
    assert result["test_stderr"] == ""
    assert tester.call_count == 2  # both gates ran


@pytest.mark.anyio
async def test_perform_merge_uses_no_edit_flag(default_config, conftest_mock_runner, tmp_path):
    """The merge command must include --no-edit for headless automation."""
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[TestResult(True, "ok", ""), TestResult(True, "ok", "")])
    # Queue all 10 steps for success path
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges (5.6 — no merge commits)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)
    conftest_mock_runner.push(_make_result(0, f"worktree {fake_wt}\n", ""))  # wt list
    conftest_mock_runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt,
        "dev",
        config=default_config,
        runner=conftest_mock_runner,
        tester=tester,
    )
    assert result.get("merge_succeeded") is True

    # Find the merge command in call_args_list
    merge_cmds = [
        args[0]
        for args in conftest_mock_runner.call_args_list
        if "merge" in args[0] and "--abort" not in args[0]
    ]
    assert len(merge_cmds) == 1
    assert "--no-edit" in merge_cmds[0], (
        f"Expected --no-edit in merge command, got: {merge_cmds[0]}"
    )


@pytest.mark.anyio
async def test_perform_merge_blocks_on_missing_remote_tracking_ref(
    conftest_mock_runner, default_config, tmp_path
):
    """perform_merge() returns PRE_REBASE_CHECK failure when base branch
    has no remote tracking ref after fetch."""
    from autoskillit.core.types import MergeFailedStep, MergeState
    from autoskillit.server.git import perform_merge

    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()
    tester = StatefulMockTester(results=[TestResult(True, "= 10 passed =", "")])
    # Step 2: worktree verified (needs /worktrees/ in git-dir path)
    conftest_mock_runner.push(_make_result(0, str(tmp_path / ".git/worktrees/wt"), ""))
    # Step 3: branch name found
    conftest_mock_runner.push(_make_result(0, "feat/my-feature\n", ""))
    # Step 3c: git ls-files (no tracked generated files)
    conftest_mock_runner.push(_make_result(0, "", ""))
    # Step 3d: dirty tree check (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))
    # Step 4: test gate handled by tester (not runner)
    # Step 5: fetch succeeds
    conftest_mock_runner.push(_make_result(0, "", ""))
    # Step 5.5: remote tracking ref MISSING
    conftest_mock_runner.push(_make_result(128, "", "fatal: Needed a single revision"))

    result = await perform_merge(
        str(worktree_dir),
        "feature/local-only",
        config=default_config,
        runner=conftest_mock_runner,
        tester=tester,
    )

    assert result["failed_step"] == MergeFailedStep.PRE_REBASE_CHECK  # not REBASE
    assert result["state"] == MergeState.WORKTREE_INTACT_BASE_NOT_PUBLISHED
    assert "feature/local-only" in result["error"]
    assert "push" in result["error"].lower()


@pytest.mark.anyio
async def test_perform_merge_strips_tracked_generated_files(
    default_config, conftest_mock_runner, tmp_path
):
    """perform_merge() strips tracked generated files before dirty check and rebase."""
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[TestResult(True, "ok", ""), TestResult(True, "ok", "")])
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(
        _make_result(0, "src/autoskillit/hooks/hooks.json\n", "")
    )  # git ls-files (generated file found) — before dirty check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git rm --cached
    conftest_mock_runner.push(_make_result(0, "", ""))  # git commit --no-verify
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges (5.6 — no merge commits)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(_make_result(0, f"worktree {fake_wt}\n", ""))  # wt list
    conftest_mock_runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt, "dev", config=default_config, runner=conftest_mock_runner, tester=tester
    )
    assert result.get("merge_succeeded") is True

    # Verify git ls-files was called before dirty check and rebase
    ls_cmds = [args[0] for args in conftest_mock_runner.call_args_list if "ls-files" in args[0]]
    assert len(ls_cmds) == 1

    # Verify git rm --cached was called with the tracked file
    rm_cmds = [
        args[0]
        for args in conftest_mock_runner.call_args_list
        if "rm" in args[0] and "--cached" in args[0]
    ]
    assert len(rm_cmds) == 1
    assert "src/autoskillit/hooks/hooks.json" in rm_cmds[0]

    # Verify git commit --no-verify was called
    commit_cmds = [
        args[0]
        for args in conftest_mock_runner.call_args_list
        if "commit" in args[0] and "--no-verify" in args[0]
    ]
    assert len(commit_cmds) == 1


@pytest.mark.anyio
async def test_perform_merge_noop_when_no_generated_files_tracked(
    default_config, conftest_mock_runner, tmp_path
):
    """perform_merge() skips cleanup when no generated files are tracked."""
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[TestResult(True, "ok", ""), TestResult(True, "ok", "")])
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (empty — no generated files)
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges (5.6 — no merge commits)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(_make_result(0, f"worktree {fake_wt}\n", ""))  # wt list
    conftest_mock_runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt, "dev", config=default_config, runner=conftest_mock_runner, tester=tester
    )
    assert result.get("merge_succeeded") is True

    # git rm --cached and git commit should NOT have been called
    rm_cmds = [
        args[0]
        for args in conftest_mock_runner.call_args_list
        if "rm" in args[0] and "--cached" in args[0]
    ]
    assert len(rm_cmds) == 0


@pytest.mark.anyio
async def test_perform_merge_fails_on_generated_file_cleanup_error(
    default_config, conftest_mock_runner, tmp_path
):
    """perform_merge() fails with GENERATED_FILE_CLEANUP when git rm --cached fails."""
    from autoskillit.core.types import MergeFailedStep, MergeState
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[TestResult(True, "ok", "")])
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(
        _make_result(0, "src/autoskillit/hooks/hooks.json\n", "")
    )  # git ls-files (generated file found) — before dirty check
    conftest_mock_runner.push(_make_result(1, "", "error: pathspec"))  # git rm --cached fails

    result = await perform_merge(
        fake_wt, "dev", config=default_config, runner=conftest_mock_runner, tester=tester
    )
    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.GENERATED_FILE_CLEANUP
    assert result["state"] == MergeState.WORKTREE_INTACT


@pytest.mark.anyio
async def test_perform_merge_dirty_check_ignores_generated_files(
    default_config, conftest_mock_runner, tmp_path
):
    """Dirty-tree check must pass when only generated-file paths are dirty.

    A worktree with only generated diagram files showing in git status
    should not block the merge.
    """
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[TestResult(True, "ok", ""), TestResult(True, "ok", "")])
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (no tracked generated files)
    # git status --porcelain returns only a generated config file as untracked
    dirty_out = "?? src/autoskillit/hooks/hooks.json\n"
    conftest_mock_runner.push(_make_result(0, dirty_out, ""))  # git status --porcelain
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(_make_result(0, f"worktree {fake_wt}\n", ""))  # wt list
    conftest_mock_runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt, "dev", config=default_config, runner=conftest_mock_runner, tester=tester
    )
    assert result.get("merge_succeeded") is True
    assert "dirty_files" not in result


@pytest.mark.anyio
async def test_perform_merge_strips_generated_files_before_dirty_check(
    default_config, conftest_mock_runner, tmp_path
):
    """Generated file strip must occur before the dirty-tree check and before rebase.

    Verifies call ordering: ls-files → rm --cached → commit → status --porcelain
    → ... → rebase.
    """
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[TestResult(True, "ok", ""), TestResult(True, "ok", "")])
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(
        _make_result(0, "src/autoskillit/recipes/diagrams/implementation.md\n", "")
    )  # git ls-files (tracked diagram file found)
    conftest_mock_runner.push(_make_result(0, "", ""))  # git rm --cached
    conftest_mock_runner.push(_make_result(0, "", ""))  # git commit --no-verify
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(_make_result(0, f"worktree {fake_wt}\n", ""))  # wt list
    conftest_mock_runner.push(_make_result(0, "dev\n", ""))  # step 7.5: branch --show-current
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt, "dev", config=default_config, runner=conftest_mock_runner, tester=tester
    )
    assert result.get("merge_succeeded") is True

    calls = [args[0] for args in conftest_mock_runner.call_args_list]
    ls_files_idx = next(i for i, c in enumerate(calls) if "ls-files" in c)
    status_idx = next(i for i, c in enumerate(calls) if "--porcelain" in c)
    rebase_idx = next(i for i, c in enumerate(calls) if "rebase" in c and "--autostash" in c)
    assert ls_files_idx < status_idx < rebase_idx


def _make_tester() -> StatefulMockTester:
    """Return a StatefulMockTester with two passing test results (pre- and post-rebase)."""
    return StatefulMockTester(
        results=[
            TestResult(True, "PASS\n= 10 passed =", ""),
            TestResult(True, "PASS\n= 10 passed =", ""),
        ]
    )


def _push_full_success_sequence(
    runner: "MockSubprocessRunner",
    *,
    worktree_path: "Path",  # noqa: F821
    base_branch: str = "dev",
) -> None:
    """Push the git subprocess sequence for a successful merge onto runner.

    Covers all git calls in perform_merge (steps 2-9, 11, 12). The test gate
    (step 4) is handled by StatefulMockTester, not via the runner.
    Cleanup steps (remove_git_worktree, branch -D) use the runner default (rc=0).
    """
    runner.push(_make_result(0, "/repo/.git/worktrees/impl-test\n"))  # rev-parse --git-dir
    runner.push(_make_result(0, "impl-test\n"))  # branch --show-current (worktree)
    runner.push(_make_result(0, ""))  # git ls-files (generated)
    runner.push(_make_result(0, ""))  # git status --porcelain
    runner.push(_make_result(0, ""))  # git fetch
    runner.push(_make_result(0, "abc123\n"))  # rev-parse --verify
    runner.push(_make_result(0, ""))  # git log --merges
    runner.push(_make_result(0, ""))  # git rebase
    runner.push(  # worktree list --porcelain
        _make_result(
            0, f"worktree /repo\nHEAD abc123\nbranch refs/heads/{base_branch}\n\n"
        )
    )
    runner.push(_make_result(0, f"{base_branch}\n"))  # step 7.5: branch --show-current (main_repo)
    runner.push(_make_result(0, ""))  # git merge --no-edit


class TestPerformMergeSidecarCleanup:
    """perform_merge removes the .autoskillit/temp/worktrees/<name>/ sidecar on success."""

    @pytest.mark.anyio
    async def test_sidecar_removed_on_successful_merge(self, tmp_path):
        """After a successful merge, remove_worktree_sidecar is called with the branch name."""
        from autoskillit.server.git import perform_merge

        wt = tmp_path / "worktrees" / "impl-test"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

        sidecar_calls = []
        with patch(
            "autoskillit.server.git.remove_worktree_sidecar",
            side_effect=lambda proj, name: (
                sidecar_calls.append(name) or CleanupResult(deleted=["s"])
            ),
        ):
            runner = MockSubprocessRunner()
            _push_full_success_sequence(runner, worktree_path=wt)
            result = await perform_merge(
                str(wt),
                "dev",
                config=AutomationConfig(),
                runner=runner,
                tester=_make_tester(),
            )

        assert result["merge_succeeded"] is True
        assert "impl-test" in sidecar_calls

    @pytest.mark.anyio
    async def test_sidecar_cleanup_failure_does_not_block_merge_result(self, tmp_path):
        """If sidecar removal fails, the merge result is still reported as succeeded."""
        from autoskillit.server.git import perform_merge

        wt = tmp_path / "worktrees" / "impl-test"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

        with patch(
            "autoskillit.server.git.remove_worktree_sidecar",
            return_value=CleanupResult(failed=[("/some/path", "permission denied")]),
        ):
            runner = MockSubprocessRunner()
            _push_full_success_sequence(runner, worktree_path=wt)
            result = await perform_merge(
                str(wt),
                "dev",
                config=AutomationConfig(),
                runner=runner,
                tester=_make_tester(),
            )

        assert result["merge_succeeded"] is True

    @pytest.mark.anyio
    async def test_uses_remove_git_worktree_from_workspace(self, tmp_path):
        """perform_merge delegates worktree removal to workspace.worktree.remove_git_worktree."""
        from autoskillit.server.git import perform_merge

        wt = tmp_path / "worktrees" / "impl-test"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

        remove_calls = []

        async def _fake_remove(path, repo, runner):
            remove_calls.append(path)
            return CleanupResult(deleted=[str(path)])

        with patch(
            "autoskillit.server.git.remove_git_worktree", new=AsyncMock(side_effect=_fake_remove)
        ):
            runner = MockSubprocessRunner()
            _push_full_success_sequence(runner, worktree_path=wt)
            await perform_merge(
                str(wt),
                "dev",
                config=AutomationConfig(),
                runner=runner,
                tester=_make_tester(),
            )

        assert wt in remove_calls


class TestPerformMergeTargetBranchVerification:
    """perform_merge must verify main_repo's checked-out branch matches base_branch."""

    @pytest.mark.anyio
    async def test_rejects_merge_when_main_repo_on_wrong_branch(self, tmp_path):
        """Test 1.1: Reproduce the primary bug — worktree list says 'main' but
        base_branch='dev'. Step 7.5 must reject the mismatch."""
        from autoskillit.server.git import perform_merge

        wt = tmp_path / "worktrees" / "impl-test"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

        runner = MockSubprocessRunner()
        tester = _make_tester()

        runner.push(_make_result(0, "/repo/.git/worktrees/impl-test\n"))  # rev-parse
        runner.push(_make_result(0, "impl-test\n"))  # branch --show-current (worktree)
        runner.push(_make_result(0, ""))  # git ls-files (generated)
        runner.push(_make_result(0, ""))  # git status --porcelain
        runner.push(_make_result(0, ""))  # git fetch
        runner.push(_make_result(0, "abc123\n"))  # rev-parse --verify
        runner.push(_make_result(0, ""))  # git log --merges
        runner.push(_make_result(0, ""))  # git rebase
        # worktree list says main_repo is on 'main'
        runner.push(
            _make_result(0, "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n\n")
        )
        # Step 7.5: git branch --show-current on main_repo returns 'main'
        runner.push(_make_result(0, "main\n"))

        result = await perform_merge(
            str(wt),
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=tester,
        )

        assert "error" in result
        assert result["failed_step"] == MergeFailedStep.MERGE
        assert result["state"] == MergeState.WORKTREE_INTACT
        assert "main" in result["error"]
        assert "dev" in result["error"]
        # No merge command should have been issued
        merge_cmds = [
            args[0]
            for args in runner.call_args_list
            if len(args[0]) > 1 and args[0][1] == "merge"
        ]
        assert merge_cmds == [], f"Merge command was issued despite branch mismatch: {merge_cmds}"

    @pytest.mark.anyio
    async def test_succeeds_when_main_repo_branch_matches(self, tmp_path):
        """Test 1.2: Happy path — worktree list and step 7.5 both say 'dev'."""
        from autoskillit.server.git import perform_merge

        wt = tmp_path / "worktrees" / "impl-test"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

        runner = MockSubprocessRunner()
        tester = _make_tester()

        runner.push(_make_result(0, "/repo/.git/worktrees/impl-test\n"))  # rev-parse
        runner.push(_make_result(0, "impl-test\n"))  # branch --show-current (worktree)
        runner.push(_make_result(0, ""))  # git ls-files (generated)
        runner.push(_make_result(0, ""))  # git status --porcelain
        runner.push(_make_result(0, ""))  # git fetch
        runner.push(_make_result(0, "abc123\n"))  # rev-parse --verify
        runner.push(_make_result(0, ""))  # git log --merges
        runner.push(_make_result(0, ""))  # git rebase
        # worktree list says main_repo is on 'dev'
        runner.push(
            _make_result(0, "worktree /repo\nHEAD abc123\nbranch refs/heads/dev\n\n")
        )
        # Step 7.5: git branch --show-current on main_repo returns 'dev'
        runner.push(_make_result(0, "dev\n"))
        runner.push(_make_result(0, ""))  # git merge --no-edit
        # cleanup: remove_git_worktree + branch -D use runner defaults (rc=0)

        result = await perform_merge(
            str(wt),
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=tester,
        )

        assert result["merge_succeeded"] is True
        assert result["into_branch"] == "dev"

    @pytest.mark.anyio
    async def test_merge_command_cwd_is_main_repo(self, tmp_path):
        """Test 1.3: Assert git merge --no-edit is executed with cwd == main_repo path."""
        from pathlib import Path

        from autoskillit.server.git import perform_merge

        wt = tmp_path / "worktrees" / "impl-test"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

        runner = MockSubprocessRunner()
        tester = _make_tester()

        runner.push(_make_result(0, "/repo/.git/worktrees/impl-test\n"))  # rev-parse
        runner.push(_make_result(0, "impl-test\n"))  # branch --show-current (worktree)
        runner.push(_make_result(0, ""))  # git ls-files (generated)
        runner.push(_make_result(0, ""))  # git status --porcelain
        runner.push(_make_result(0, ""))  # git fetch
        runner.push(_make_result(0, "abc123\n"))  # rev-parse --verify
        runner.push(_make_result(0, ""))  # git log --merges
        runner.push(_make_result(0, ""))  # git rebase
        runner.push(
            _make_result(0, "worktree /repo\nHEAD abc123\nbranch refs/heads/dev\n\n")
        )
        # Step 7.5: branch verification
        runner.push(_make_result(0, "dev\n"))
        runner.push(_make_result(0, ""))  # git merge --no-edit

        result = await perform_merge(
            str(wt),
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=tester,
        )

        assert result["merge_succeeded"] is True
        # Find the merge command and assert its cwd
        merge_call = next(
            args
            for args in runner.call_args_list
            if len(args[0]) > 1 and args[0][1] == "merge"
        )
        assert merge_call[1] == Path("/repo"), (
            f"Merge command cwd should be /repo (main_repo), got {merge_call[1]}"
        )

    @pytest.mark.anyio
    async def test_into_branch_reflects_verified_state(self, tmp_path):
        """Test 1.4: into_branch must come from verified git state, not echoed input."""
        from autoskillit.server.git import perform_merge

        wt = tmp_path / "worktrees" / "impl-test"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/impl-test")

        runner = MockSubprocessRunner()
        tester = _make_tester()

        runner.push(_make_result(0, "/repo/.git/worktrees/impl-test\n"))  # rev-parse
        runner.push(_make_result(0, "impl-test\n"))  # branch --show-current (worktree)
        runner.push(_make_result(0, ""))  # git ls-files (generated)
        runner.push(_make_result(0, ""))  # git status --porcelain
        runner.push(_make_result(0, ""))  # git fetch
        runner.push(_make_result(0, "abc123\n"))  # rev-parse --verify
        runner.push(_make_result(0, ""))  # git log --merges
        runner.push(_make_result(0, ""))  # git rebase
        runner.push(
            _make_result(0, "worktree /repo\nHEAD abc123\nbranch refs/heads/dev\n\n")
        )
        # Step 7.5: verified branch
        runner.push(_make_result(0, "dev\n"))
        runner.push(_make_result(0, ""))  # merge

        result = await perform_merge(
            str(wt),
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=tester,
        )

        assert result["merge_succeeded"] is True
        # into_branch must match the verified branch from step 7.5
        assert result["into_branch"] == "dev"
