"""Tests for server/git.py perform_merge()."""

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core.types import SubprocessResult, TerminationReason


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
def mock_runner(conftest_mock_runner):
    return conftest_mock_runner


@pytest.fixture
def conftest_mock_runner():
    from tests.conftest import MockSubprocessRunner

    return MockSubprocessRunner()


@pytest.mark.asyncio
async def test_perform_merge_returns_error_for_nonexistent_path(
    default_config, conftest_mock_runner
):
    from autoskillit.server.git import perform_merge

    result = await perform_merge(
        "/nonexistent/path", "main", config=default_config, runner=conftest_mock_runner
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_perform_merge_blocks_on_failing_tests(
    default_config, conftest_mock_runner, tmp_path
):
    from autoskillit.core.types import MergeFailedStep, MergeState
    from autoskillit.server.git import perform_merge

    # Create a temp dir to use as a fake worktree path so os.path.isdir passes
    fake_wt = str(tmp_path)
    # Queue: rev-parse (worktree ok), branch, test (fails)
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))  # rev-parse
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))  # branch
    conftest_mock_runner.push(_make_result(1, "= 1 failed =", ""))  # test

    result = await perform_merge(
        fake_wt, "main", config=default_config, runner=conftest_mock_runner
    )
    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.TEST_GATE
    assert result["state"] == MergeState.WORKTREE_INTACT


class _MockTester:
    """Mock TestRunner for perform_merge tests."""

    def __init__(self, passed: bool = True, output: str = "50 passed"):
        self._passed = passed
        self._output = output

    async def run(self, cwd):
        return (self._passed, self._output)


@pytest.mark.asyncio
async def test_perform_merge_returns_success_on_green_tests(
    default_config, conftest_mock_runner, tmp_path
):
    from autoskillit.server.git import perform_merge
    from tests.conftest import StatefulMockTester

    fake_wt = str(tmp_path)
    tester = StatefulMockTester(results=[(True, "= 50 passed ="), (True, "= 50 passed =")])
    # Queue 8 steps: rev-parse, branch, fetch, rebase, wt-list, merge, remove, branch-D
    # (test gate now handled by tester, not subprocess)
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # ref check (5.5)
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
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


@pytest.mark.asyncio
async def test_perform_merge_blocks_on_post_rebase_test_failure(
    default_config, conftest_mock_runner, tmp_path
):
    """Post-rebase test failure returns POST_REBASE_TEST_GATE and WORKTREE_INTACT."""
    from autoskillit.core.types import MergeFailedStep, MergeState
    from autoskillit.server.git import perform_merge
    from tests.conftest import StatefulMockTester

    # Pre-rebase: pass; post-rebase: fail
    tester = StatefulMockTester(results=[(True, "= 10 passed ="), (False, "= 1 failed =")])
    # Queue: rev-parse (valid worktree), branch, fetch ok, rebase ok
    # No merge/cleanup queued — gate blocks before those
    conftest_mock_runner.push(_make_result(0, f"{str(tmp_path)}/.git/worktrees/feature", ""))
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch ok
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase ok

    result = await perform_merge(
        str(tmp_path), "main", config=default_config, runner=conftest_mock_runner, tester=tester
    )

    assert "error" in result
    assert result["failed_step"] == MergeFailedStep.POST_REBASE_TEST_GATE
    assert result["state"] == MergeState.WORKTREE_INTACT
    assert "worktree_path" in result
    assert tester.call_count == 2  # both gates ran


@pytest.mark.asyncio
async def test_perform_merge_both_gates_run_on_full_success(
    default_config, conftest_mock_runner, tmp_path
):
    """On full success, both pre-rebase and post-rebase test gates execute."""
    from autoskillit.server.git import perform_merge
    from tests.conftest import StatefulMockTester

    tester = StatefulMockTester(results=[(True, "= 10 passed ="), (True, "= 10 passed =")])
    # Full queue (tester is injected; no test-check in subprocess queue)
    conftest_mock_runner.push(_make_result(0, f"{str(tmp_path)}/.git/worktrees/feat", ""))
    conftest_mock_runner.push(_make_result(0, "feat\n", ""))
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
    conftest_mock_runner.push(_make_result(0, "", ""))  # rebase
    conftest_mock_runner.push(_make_result(0, f"worktree {str(tmp_path)}\n", ""))  # wt-list
    conftest_mock_runner.push(_make_result(0, "", ""))  # merge
    conftest_mock_runner.push(_make_result(0, "", ""))  # wt-remove
    conftest_mock_runner.push(_make_result(0, "", ""))  # branch -D

    result = await perform_merge(
        str(tmp_path), "main", config=default_config, runner=conftest_mock_runner, tester=tester
    )

    assert result.get("merge_succeeded") is True
    assert tester.call_count == 2
