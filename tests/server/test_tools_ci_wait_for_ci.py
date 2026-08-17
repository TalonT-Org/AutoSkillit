"""Tests for the wait_for_ci MCP tool (success/failure, head_sha inference,
event/lookback forwarding, remote_url, exception handling, timing).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import autoskillit.server.tools.tools_ci_watch as tools_ci_watch
from autoskillit.core import SubprocessResult, TerminationReason
from autoskillit.server.tools.tools_ci_watch import wait_for_ci
from tests.fakes import InMemoryCIWatcher
from tests.server._recipe_segment_test_helpers import install_prepared_recipe_segment
from tests.server.conftest import assert_no_timing, assert_step_timed

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# wait_for_ci success / failure responses
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_success_response(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
):
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 12345, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    tool_ctx_kitchen_open.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="abc123\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )
    install_prepared_recipe_segment(monkeypatch, tools_ci_watch, step_name="ci")

    result = json.loads(await wait_for_ci("main", cwd="/some/repo", step_name="ci"))

    assert result["run_id"] == 12345
    assert result["conclusion"] == "success"
    assert result["failed_jobs"] == []
    assert result["recipe_segment"]["kind"] == "success"


@pytest.mark.anyio
async def test_wait_for_ci_failure_response(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
):
    watcher = InMemoryCIWatcher(
        wait_result={
            "run_id": 12345,
            "conclusion": "failure",
            "failed_jobs": ["test", "lint"],
        }
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    tool_ctx_kitchen_open.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="abc123\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )
    install_prepared_recipe_segment(monkeypatch, tools_ci_watch, step_name="ci")

    result = json.loads(await wait_for_ci("main", cwd="/some/repo", step_name="ci"))

    assert result["conclusion"] == "failure"
    assert sorted(result["failed_jobs"]) == ["lint", "test"]
    assert result["recipe_segment"]["kind"] == "recovery"


# ---------------------------------------------------------------------------
# wait_for_ci head_sha inference
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_infers_head_sha(tool_ctx_kitchen_open, tmp_path):
    """When head_sha is not provided, it's inferred via git rev-parse HEAD."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    tool_ctx_kitchen_open.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="abc123\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    await wait_for_ci("main", cwd=str(tmp_path))

    # Verify that wait was called with the inferred head_sha inside scope
    assert watcher.wait_calls[-1]["scope"].head_sha == "abc123"


@pytest.mark.anyio
async def test_wait_for_ci_head_sha_uses_runner(tool_ctx_kitchen_open, tmp_path):
    """git rev-parse HEAD must flow through MockSubprocessRunner, not raw asyncio."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher

    # Pre-configure runner to return a valid SHA when git rev-parse is called
    tool_ctx_kitchen_open.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="deadbeef\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    await wait_for_ci("main", cwd=str(tmp_path))

    # Runner must have been called with the git command
    assert tool_ctx_kitchen_open.runner.call_args_list, "runner was never called"
    cmd = tool_ctx_kitchen_open.runner.call_args_list[0][0]
    assert cmd == ["git", "rev-parse", "HEAD"], f"Unexpected runner call: {cmd}"

    # SHA extracted from runner output must have been passed to the CI watcher
    assert watcher.wait_calls[-1]["scope"].head_sha == "deadbeef"


# ---------------------------------------------------------------------------
# wait_for_ci with no ci_watcher
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_no_watcher(tool_ctx_kitchen_open):
    tool_ctx_kitchen_open.ci_watcher = None
    result = json.loads(await wait_for_ci("main"))
    assert result["conclusion"] == "error"
    assert "not configured" in result["error"]


# ---------------------------------------------------------------------------
# wait_for_ci event param propagation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_passes_event_to_scope(tool_ctx_kitchen_open):
    """wait_for_ci must propagate event param into CIRunScope."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    await wait_for_ci(branch="main", event="push", cwd="/tmp")
    assert len(watcher.wait_calls) == 1
    assert watcher.wait_calls[-1]["scope"].event == "push"


# ---------------------------------------------------------------------------
# wait_for_ci lookback_seconds forwarding
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_forwards_lookback_seconds(tool_ctx_kitchen_open):
    """wait_for_ci must propagate lookback_seconds to ci_watcher.wait()."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    await wait_for_ci(branch="main", lookback_seconds=7200, cwd="/tmp")
    assert watcher.wait_calls[-1]["lookback_seconds"] == 7200


@pytest.mark.anyio
async def test_wait_for_ci_lookback_defaults_to_3600(tool_ctx_kitchen_open):
    """wait_for_ci default lookback_seconds is 3600 (1 hour)."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    await wait_for_ci(branch="main", cwd="/tmp")
    assert watcher.wait_calls[-1]["lookback_seconds"] == 3600


# ---------------------------------------------------------------------------
# wait_for_ci: remote_url parameter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_parses_remote_url_to_resolve_repo(tool_ctx_kitchen_open):
    """When remote_url is provided, wait_for_ci must parse it to owner/repo
    and pass that to the watcher without calling any subprocess."""
    watcher = InMemoryCIWatcher(
        wait_result={
            "conclusion": "success",
            "run_id": 1,
            "failed_jobs": [],
            "head_sha": "abc123",
        }
    )
    tool_ctx_kitchen_open.ci_watcher = watcher

    result = json.loads(
        await wait_for_ci(
            branch="main",
            remote_url="https://github.com/owner/repo.git",
            cwd="/any/cwd",
        )
    )
    assert result["conclusion"] == "success"
    assert watcher.wait_calls[-1].get("repo") == "owner/repo"


@pytest.mark.anyio
async def test_wait_for_ci_remote_url_wins_over_empty_repo(tool_ctx_kitchen_open):
    """remote_url= supersedes repo='' — hint priority in resolve_remote_repo."""
    watcher = InMemoryCIWatcher(
        wait_result={
            "conclusion": "success",
            "run_id": 1,
            "failed_jobs": [],
            "head_sha": "abc",
        }
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    await wait_for_ci(
        branch="main",
        remote_url="https://github.com/owner/repo.git",
        repo="",  # empty — remote_url must win
        cwd="/any/cwd",
    )
    assert watcher.wait_calls[-1].get("repo") == "owner/repo"


# ---------------------------------------------------------------------------
# MCP handler — workflow passed via scope (moved from execution/test_ci_params)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_handler_passes_workflow(tool_ctx_kitchen_open):
    """wait_for_ci MCP handler must forward workflow to watcher via scope."""
    watcher = InMemoryCIWatcher(
        wait_result={"conclusion": "success", "failed_jobs": [], "run_id": 1}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher

    # cwd="" → head_sha inference skipped (empty string is falsy)
    json.loads(await wait_for_ci(branch="main", workflow="tests.yml", cwd=""))

    assert len(watcher.wait_calls) == 1
    assert watcher.wait_calls[-1]["scope"].workflow == "tests.yml"


# ---------------------------------------------------------------------------
# C9-1: exception boundary — bare raise replaced with structured JSON return
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_watcher_exception_returns_structured_json(tool_ctx_kitchen_open):
    """wait_for_ci returns structured JSON with conclusion='error' when watcher.wait() raises.

    BEFORE fix: bare raise propagates to track_response_size which adds
    subtype='tool_exception'. AFTER fix: explicit return gives clean JSON.
    """
    watcher = InMemoryCIWatcher()
    watcher.wait_side_effect = RuntimeError("network timeout")
    tool_ctx_kitchen_open.ci_watcher = watcher

    with patch(
        "autoskillit.execution.remote_resolver.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(return_value=(b"", b""))
        proc_inst.returncode = 1
        mock_proc.return_value = proc_inst

        result = json.loads(await wait_for_ci("main", cwd="/some/repo"))

    assert result["conclusion"] == "error"
    assert "network timeout" in result["error"]
    assert "subtype" not in result  # no decorator fallback marker
    assert "exit_code" not in result  # no decorator fallback marker


# ---------------------------------------------------------------------------
# wait_for_ci head_sha enrichment (Gap 5)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_includes_head_sha_in_result(tool_ctx_kitchen_open, tmp_path):
    """wait_for_ci result includes head_sha when git rev-parse HEAD succeeds."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    tool_ctx_kitchen_open.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="deadbeef1234\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    result = json.loads(await wait_for_ci("main", cwd=str(tmp_path)))

    assert result["head_sha"] == "deadbeef1234"


@pytest.mark.anyio
async def test_wait_for_ci_omits_head_sha_when_git_fails(tool_ctx_kitchen_open):
    """wait_for_ci result omits head_sha when git rev-parse fails."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx_kitchen_open.ci_watcher = watcher
    tool_ctx_kitchen_open.runner.push(
        SubprocessResult(
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    result = json.loads(await wait_for_ci("main", cwd="/some/repo"))

    assert "head_sha" not in result
    assert "exit_code" not in result


# ---------------------------------------------------------------------------
# C9-2: exception path shape — conclusion key required for recipe on_result routing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_exception_returns_conclusion_key(tool_ctx_kitchen_open, monkeypatch):
    """Inner exception path must return conclusion='error' for recipe on_result routing."""

    async def _exploding_wait(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool_ctx_kitchen_open.ci_watcher, "wait", _exploding_wait)
    raw = await wait_for_ci(branch="main", cwd="/tmp")
    result = json.loads(raw)
    assert "conclusion" in result, "Exception path must include conclusion key"
    assert result["conclusion"] == "error"
    assert "error" in result


class TestWaitForCiTiming:
    """wait_for_ci records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_wait_for_ci_step_name_records_timing(self, tool_ctx_kitchen_open):
        watcher = InMemoryCIWatcher(
            wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
        )
        tool_ctx_kitchen_open.ci_watcher = watcher
        await wait_for_ci("main", step_name="ci_wait")
        assert_step_timed(tool_ctx_kitchen_open.timing_log, "ci_wait")

    @pytest.mark.anyio
    async def test_wait_for_ci_empty_step_name_skips_timing(self, tool_ctx):
        watcher = InMemoryCIWatcher(
            wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
        )
        tool_ctx.ci_watcher = watcher
        await wait_for_ci("main")
        assert_no_timing(tool_ctx.timing_log)
