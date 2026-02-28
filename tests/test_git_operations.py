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

    fake_wt = str(tmp_path)
    # Queue 8 steps: rev-parse, branch, fetch, rebase, wt-list, merge, remove, branch-D
    # (test gate now handled by tester, not subprocess)
    conftest_mock_runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt", ""))
    conftest_mock_runner.push(_make_result(0, "feature-branch\n", ""))
    conftest_mock_runner.push(_make_result(0, "", ""))  # fetch
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
        tester=_MockTester(passed=True),
    )
    assert result.get("merge_succeeded") is True
    assert result["merged_branch"] == "feature-branch"
