"""Tests for server/git.py perform_merge()."""

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core.types import SubprocessResult, TerminationReason
from tests.conftest import StatefulMockTester


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
        "/nonexistent/path", "main", config=default_config, runner=conftest_mock_runner
    )
    assert "error" in result


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
    conftest_mock_runner.push(_make_result(0, " M hooks.json\n", ""))  # git status --porcelain

    result = await perform_merge(
        fake_wt, "main", config=default_config, runner=conftest_mock_runner
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
    dirty_output = " M hooks.json\n M .claude/settings.json\n?? untracked.txt\n"
    conftest_mock_runner.push(_make_result(0, dirty_output, ""))  # git status --porcelain

    result = await perform_merge(
        fake_wt, "main", config=default_config, runner=conftest_mock_runner
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
        fake_wt, "main", config=default_config, runner=conftest_mock_runner
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
    tester = StatefulMockTester(results=[(True, "= 50 passed ="), (True, "= 50 passed =")])
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
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt,
        "main",
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
    tester = StatefulMockTester(results=[(True, "= 10 passed ="), (False, "= 1 failed =")])
    # Queue: rev-parse (valid worktree), branch, dirty check, fetch ok, rebase ok
    # No merge/cleanup queued — gate blocks before those
    conftest_mock_runner.push(_make_result(0, f"{str(tmp_path)}/.git/worktrees/feature", ""))
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch ok
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check (5.5)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase ok
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (generated file check)

    result = await perform_merge(
        str(tmp_path), "main", config=default_config, runner=conftest_mock_runner, tester=tester
    )

    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.POST_REBASE_TEST_GATE
    assert result["state"] == MergeState.WORKTREE_INTACT
    assert "worktree_path" in result
    assert tester.call_count == 2  # both gates ran


@pytest.mark.anyio
async def test_perform_merge_uses_no_edit_flag(default_config, conftest_mock_runner, tmp_path):
    """The merge command must include --no-edit for headless automation."""
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[(True, "ok"), (True, "ok")])
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
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt,
        "main",
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
    tester = StatefulMockTester(results=[(True, "= 10 passed =")])
    # Step 2: worktree verified (needs /worktrees/ in git-dir path)
    conftest_mock_runner.push(_make_result(0, str(tmp_path / ".git/worktrees/wt"), ""))
    # Step 3: branch name found
    conftest_mock_runner.push(_make_result(0, "feat/my-feature\n", ""))
    # Step 3c: dirty tree check (clean)
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
    """perform_merge() strips tracked generated files after rebase."""
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[(True, "ok"), (True, "ok")])
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges (5.6 — no merge commits)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(
        _make_result(0, "src/autoskillit/hooks/hooks.json\n", "")
    )  # git ls-files (generated file found)
    conftest_mock_runner.push(_make_result(0, "", ""))  # git rm --cached
    conftest_mock_runner.push(_make_result(0, "", ""))  # git commit --no-verify
    conftest_mock_runner.push(_make_result(0, f"worktree {fake_wt}\n", ""))  # wt list
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt, "main", config=default_config, runner=conftest_mock_runner, tester=tester
    )
    assert result.get("merge_succeeded") is True

    # Verify git ls-files was called after rebase
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
    tester = StatefulMockTester(results=[(True, "ok"), (True, "ok")])
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges (5.6 — no merge commits)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(_make_result(0, "", ""))  # git ls-files (empty — no generated files)
    conftest_mock_runner.push(_make_result(0, f"worktree {fake_wt}\n", ""))  # wt list
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        fake_wt, "main", config=default_config, runner=conftest_mock_runner, tester=tester
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
    tester = StatefulMockTester(results=[(True, "ok")])
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check
    conftest_mock_runner.push(_make_result(0, "", ""))  # git log --merges (5.6 — no merge commits)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(
        _make_result(0, "src/autoskillit/hooks/hooks.json\n", "")
    )  # git ls-files (generated file found)
    conftest_mock_runner.push(_make_result(1, "", "error: pathspec"))  # git rm --cached fails

    result = await perform_merge(
        fake_wt, "main", config=default_config, runner=conftest_mock_runner, tester=tester
    )
    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.GENERATED_FILE_CLEANUP
    assert result["state"] == MergeState.WORKTREE_INTACT
