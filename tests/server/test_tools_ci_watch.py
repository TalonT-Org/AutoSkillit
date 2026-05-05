"""Tests for wait_for_ci event validation, null coercion, and auto_trigger recovery."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import SubprocessResult, TerminationReason
from autoskillit.server.tools.tools_ci_watch import wait_for_ci
from tests.fakes import InMemoryCIWatcher

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_wait_for_ci_rejects_invalid_event(tool_ctx) -> None:
    tool_ctx.ci_watcher = InMemoryCIWatcher()
    result = json.loads(await wait_for_ci("main", event="made_up_event"))
    assert result["conclusion"] == "error"
    assert "event" in result.get("error", "").lower()


@pytest.mark.anyio
async def test_wait_for_ci_coerces_string_none_to_null(tool_ctx) -> None:
    tool_ctx.ci_watcher = InMemoryCIWatcher(
        wait_result={"run_id": 42, "conclusion": "success", "failed_jobs": []}
    )
    result = json.loads(await wait_for_ci("main", event="None"))
    assert result["conclusion"] == "success", (
        f"String 'None' must be coerced to null before event validation — "
        f"KNOWN_CI_EVENTS would reject 'None' as invalid if coercion did not occur. "
        f"Got: {result}"
    )


# ---------------------------------------------------------------------------
# wait_for_ci auto_trigger
# ---------------------------------------------------------------------------

_NO_RUNS = {"conclusion": "no_runs", "run_id": None, "failed_jobs": []}
_SUCCESS = {"conclusion": "success", "run_id": 42, "failed_jobs": []}


def _sub(returncode: int, stdout: str = "", stderr: str = "") -> SubprocessResult:
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.NATURAL_EXIT,
        pid=0,
    )


class TestWaitForCiAutoTrigger:
    """wait_for_ci auto_trigger=True: active webhook recovery."""

    @pytest.mark.anyio
    async def test_auto_trigger_default_false_does_not_fire(self, tool_ctx):
        watcher = InMemoryCIWatcher(wait_result=_NO_RUNS)
        tool_ctx.ci_watcher = watcher
        tool_ctx.runner.push(_sub(0, "abc123\n"))

        result = json.loads(await wait_for_ci("branch", cwd="/repo"))

        assert len(watcher.wait_calls) == 1
        assert len(tool_ctx.runner.call_args_list) == 1
        assert result["conclusion"] == "no_runs"
        assert "triggered" not in result

    @pytest.mark.anyio
    async def test_auto_trigger_fires_on_no_runs(self, tool_ctx):
        watcher = InMemoryCIWatcher(wait_results=[_NO_RUNS, _SUCCESS])
        tool_ctx.ci_watcher = watcher
        tool_ctx.runner.push(
            _sub(0, "abc123\n")
        )  # git rev-parse HEAD — wait_for_ci initial HEAD inference
        tool_ctx.runner.push(_sub(0, '{"mergeable":"MERGEABLE"}\n'))  # gh pr view
        tool_ctx.runner.push(_sub(0))  # git commit --allow-empty
        tool_ctx.runner.push(
            _sub(0, "def456\n")
        )  # git rev-parse HEAD — new HEAD after empty commit
        tool_ctx.runner.push(
            _sub(0, "https://github.com/org/repo\n")
        )  # git remote get-url upstream
        tool_ctx.runner.push(_sub(0))  # git push --force-with-lease

        result = json.loads(await wait_for_ci("feature-branch", cwd="/repo", auto_trigger=True))

        assert len(watcher.wait_calls) == 2
        assert watcher.wait_calls[1]["scope"].head_sha == "def456"
        assert result["conclusion"] == "success"
        assert result["triggered"] is True
        assert result["head_sha"] == "def456"

    @pytest.mark.anyio
    async def test_auto_trigger_skips_on_conflicting_pr(self, tool_ctx):
        watcher = InMemoryCIWatcher(wait_result=_NO_RUNS)
        tool_ctx.ci_watcher = watcher
        tool_ctx.runner.push(_sub(0, "abc123\n"))  # git rev-parse HEAD
        tool_ctx.runner.push(_sub(0, '{"mergeable":"CONFLICTING"}\n'))  # gh pr view

        result = json.loads(await wait_for_ci("branch", cwd="/repo", auto_trigger=True))

        assert len(watcher.wait_calls) == 1
        assert result["conclusion"] == "merge_conflict"
        assert result["triggered"] is False
        assert result["run_id"] is None
        assert result["failed_jobs"] == []

    @pytest.mark.anyio
    async def test_auto_trigger_returns_gh_view_failed_on_gh_failure(self, tool_ctx):
        watcher = InMemoryCIWatcher(wait_result=_NO_RUNS)
        tool_ctx.ci_watcher = watcher
        tool_ctx.runner.push(_sub(0, "abc123\n"))  # git rev-parse HEAD (initial)
        tool_ctx.runner.push(_sub(1))  # gh pr view — CLI failure (no PR)

        result = json.loads(await wait_for_ci("branch", cwd="/repo", auto_trigger=True))

        assert len(watcher.wait_calls) == 1
        assert result["conclusion"] == "gh_view_failed"
        assert result["triggered"] is False

    @pytest.mark.anyio
    async def test_auto_trigger_commit_failure_returns_no_runs(self, tool_ctx):
        watcher = InMemoryCIWatcher(wait_result=_NO_RUNS)
        tool_ctx.ci_watcher = watcher
        tool_ctx.runner.push(_sub(0, "abc123\n"))  # git rev-parse HEAD
        tool_ctx.runner.push(_sub(0, '{"mergeable":"MERGEABLE"}\n'))  # gh pr view
        tool_ctx.runner.push(
            _sub(128, stderr="error: pre-commit hook rejected commit")
        )  # git commit fails

        result = json.loads(await wait_for_ci("branch", cwd="/repo", auto_trigger=True))

        assert len(watcher.wait_calls) == 1
        assert result["conclusion"] == "no_runs"
        assert "triggered" not in result

    @pytest.mark.anyio
    async def test_auto_trigger_push_failure_returns_no_runs(self, tool_ctx):
        watcher = InMemoryCIWatcher(wait_result=_NO_RUNS)
        tool_ctx.ci_watcher = watcher
        tool_ctx.runner.push(_sub(0, "abc123\n"))  # git rev-parse HEAD
        tool_ctx.runner.push(_sub(0, '{"mergeable":"MERGEABLE"}\n'))  # gh pr view
        tool_ctx.runner.push(_sub(0))  # git commit --allow-empty
        tool_ctx.runner.push(_sub(0, "def456\n"))  # git rev-parse HEAD (new)
        tool_ctx.runner.push(
            _sub(0, "https://github.com/org/repo\n")
        )  # git remote get-url upstream
        tool_ctx.runner.push(_sub(1, stderr="error: remote rejected"))  # git push fails
        tool_ctx.runner.push(_sub(0))  # git reset --soft HEAD~1 (cleanup)

        result = json.loads(await wait_for_ci("branch", cwd="/repo", auto_trigger=True))

        reset_cmd = tool_ctx.runner.call_args_list[-1][0]
        assert reset_cmd == ["git", "reset", "--soft", "HEAD~1"], (
            f"Expected reset, got: {reset_cmd}"
        )
        assert len(watcher.wait_calls) == 1
        assert result["conclusion"] == "no_runs"
        assert "triggered" not in result

    @pytest.mark.anyio
    async def test_auto_trigger_ci_poll_exception_returns_auto_trigger_failed(self, tool_ctx):
        call_count_holder = [0]

        def poll_raises_on_second_call() -> dict:
            call_count_holder[0] += 1
            if call_count_holder[0] >= 2:
                raise RuntimeError("auto_trigger poll failed")
            return _NO_RUNS

        watcher = InMemoryCIWatcher(wait_result=_NO_RUNS)
        watcher.wait_side_effect = poll_raises_on_second_call
        tool_ctx.ci_watcher = watcher
        tool_ctx.runner.push(
            _sub(0, "abc123\n")
        )  # git rev-parse HEAD — wait_for_ci initial HEAD inference
        tool_ctx.runner.push(_sub(0, '{"mergeable":"MERGEABLE"}\n'))  # gh pr view
        tool_ctx.runner.push(_sub(0))  # git commit --allow-empty
        tool_ctx.runner.push(
            _sub(0, "def456\n")
        )  # git rev-parse HEAD — new HEAD after empty commit
        tool_ctx.runner.push(
            _sub(0, "https://github.com/org/repo\n")
        )  # git remote get-url upstream
        tool_ctx.runner.push(_sub(0))  # git push --force-with-lease

        result = json.loads(await wait_for_ci("branch", cwd="/repo", auto_trigger=True))

        assert len(watcher.wait_calls) == 2
        assert result["conclusion"] == "auto_trigger_failed"
        assert result["triggered"] is False

    @pytest.mark.anyio
    async def test_auto_trigger_ci_forwards_lookback_seconds(self, tool_ctx):
        """The second wait() call in _auto_trigger_ci must forward lookback_seconds."""
        watcher = InMemoryCIWatcher(wait_results=[_NO_RUNS, _SUCCESS])
        tool_ctx.ci_watcher = watcher
        tool_ctx.runner.push(_sub(0, "abc123\n"))  # git rev-parse HEAD
        tool_ctx.runner.push(_sub(0, '{"mergeable":"MERGEABLE"}\n'))  # gh pr view
        tool_ctx.runner.push(_sub(0))  # git commit --allow-empty
        tool_ctx.runner.push(_sub(0, "def456\n"))  # git rev-parse HEAD — new HEAD
        tool_ctx.runner.push(
            _sub(0, "https://github.com/org/repo\n")
        )  # git remote get-url upstream
        tool_ctx.runner.push(_sub(0))  # git push --force-with-lease

        await wait_for_ci("branch", cwd="/repo", auto_trigger=True, lookback_seconds=7200)

        assert len(watcher.wait_calls) == 2
        assert watcher.wait_calls[0]["lookback_seconds"] == 7200
        assert watcher.wait_calls[1]["lookback_seconds"] == 7200
