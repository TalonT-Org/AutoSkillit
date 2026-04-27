"""Tests for the wait_for_ci, get_ci_status, and wait_for_merge_queue MCP tool handlers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from autoskillit.core import PRState, SubprocessResult, TerminationReason
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.tools_ci import (
    check_repo_merge_state,
    get_ci_status,
    wait_for_ci,
    wait_for_merge_queue,
)
from tests.fakes import InMemoryCIWatcher, InMemoryMergeQueueWatcher

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Gate membership
# ---------------------------------------------------------------------------


def test_wait_for_ci_is_gated():
    assert "wait_for_ci" in GATED_TOOLS


def test_get_ci_status_is_gated():
    assert "get_ci_status" in GATED_TOOLS
    assert "get_ci_status" not in UNGATED_TOOLS


# ---------------------------------------------------------------------------
# wait_for_ci gate check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_gate_check(tool_ctx):
    """Gate-closed returns gate_error response."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await wait_for_ci("main"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


# ---------------------------------------------------------------------------
# wait_for_ci success / failure responses
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_success_response(tool_ctx):
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 12345, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx.ci_watcher = watcher
    tool_ctx.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="abc123\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    result = json.loads(await wait_for_ci("main", cwd="/some/repo"))

    assert result["run_id"] == 12345
    assert result["conclusion"] == "success"
    assert result["failed_jobs"] == []


@pytest.mark.anyio
async def test_wait_for_ci_failure_response(tool_ctx):
    watcher = InMemoryCIWatcher(
        wait_result={
            "run_id": 12345,
            "conclusion": "failure",
            "failed_jobs": ["test", "lint"],
        }
    )
    tool_ctx.ci_watcher = watcher
    tool_ctx.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="abc123\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    result = json.loads(await wait_for_ci("main", cwd="/some/repo"))

    assert result["conclusion"] == "failure"
    assert sorted(result["failed_jobs"]) == ["lint", "test"]


# ---------------------------------------------------------------------------
# wait_for_ci head_sha inference
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_infers_head_sha(tool_ctx):
    """When head_sha is not provided, it's inferred via git rev-parse HEAD."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx.ci_watcher = watcher
    tool_ctx.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="abc123\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    await wait_for_ci("main", cwd="/some/repo")

    # Verify that wait was called with the inferred head_sha inside scope
    assert watcher.wait_calls[-1]["scope"].head_sha == "abc123"


@pytest.mark.anyio
async def test_wait_for_ci_head_sha_uses_runner(tool_ctx):
    """git rev-parse HEAD must flow through MockSubprocessRunner, not raw asyncio."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx.ci_watcher = watcher

    # Pre-configure runner to return a valid SHA when git rev-parse is called
    tool_ctx.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="deadbeef\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    await wait_for_ci("main", cwd="/some/repo")

    # Runner must have been called with the git command
    assert tool_ctx.runner.call_args_list, "runner was never called"
    cmd = tool_ctx.runner.call_args_list[0][0]
    assert cmd == ["git", "rev-parse", "HEAD"], f"Unexpected runner call: {cmd}"

    # SHA extracted from runner output must have been passed to the CI watcher
    assert watcher.wait_calls[-1]["scope"].head_sha == "deadbeef"


# ---------------------------------------------------------------------------
# wait_for_ci with no ci_watcher
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_no_watcher(tool_ctx):
    tool_ctx.ci_watcher = None
    result = json.loads(await wait_for_ci("main"))
    assert result["conclusion"] == "error"
    assert "not configured" in result["error"]


# ---------------------------------------------------------------------------
# get_ci_status (ungated)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_ci_status_gate_check(tool_ctx):
    """get_ci_status is now gated — returns gate_error when gate is closed."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await get_ci_status(branch="main", cwd="/repo"))
    assert result.get("subtype") == "gate_error"


@pytest.mark.anyio
async def test_get_ci_status_missing_branch_and_run_id(tool_ctx):
    watcher = InMemoryCIWatcher()
    tool_ctx.ci_watcher = watcher

    result = json.loads(await get_ci_status())
    assert result["runs"] == []
    assert "error" in result


@pytest.mark.anyio
async def test_get_ci_status_no_watcher(tool_ctx):
    tool_ctx.ci_watcher = None
    result = json.loads(await get_ci_status(branch="main"))
    assert result["runs"] == []
    assert "not configured" in result["error"]


# ---------------------------------------------------------------------------
# wait_for_ci event param propagation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_passes_event_to_scope(tool_ctx):
    """wait_for_ci must propagate event param into CIRunScope."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx.ci_watcher = watcher
    await wait_for_ci(branch="main", event="push", cwd="/tmp")
    assert len(watcher.wait_calls) == 1
    assert watcher.wait_calls[-1]["scope"].event == "push"


# ---------------------------------------------------------------------------
# wait_for_merge_queue
# ---------------------------------------------------------------------------


def test_wait_for_merge_queue_is_gated():
    assert "wait_for_merge_queue" in GATED_TOOLS


@pytest.mark.anyio
async def test_gate_closed_returns_gate_error(tool_ctx):
    """Gate-closed returns gate_error response (watcher not called)."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await wait_for_merge_queue(pr_number=1, target_branch="main", cwd="."))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_delegates_to_merge_queue_watcher(tool_ctx):
    watcher = InMemoryMergeQueueWatcher(
        wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx.merge_queue_watcher = watcher

    with patch(
        "autoskillit.execution.remote_resolver.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(
            return_value=(b"https://github.com/owner/repo.git\n", b"")
        )
        proc_inst.returncode = 0
        mock_proc.return_value = proc_inst

        result = json.loads(
            await wait_for_merge_queue(pr_number=42, target_branch="integration", cwd=".")
        )

    assert result["pr_state"] == "merged"
    assert len(watcher.wait_calls) == 1
    assert watcher.wait_calls[-1]["pr_number"] == 42
    assert watcher.wait_calls[-1]["target_branch"] == "integration"


@pytest.mark.anyio
async def test_infers_repo_from_git_remote_when_repo_empty(tool_ctx):
    watcher = InMemoryMergeQueueWatcher(
        wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx.merge_queue_watcher = watcher

    with patch(
        "autoskillit.execution.remote_resolver.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(
            return_value=(b"https://github.com/owner/repo.git\n", b"")
        )
        proc_inst.returncode = 0
        mock_proc.return_value = proc_inst

        await wait_for_merge_queue(pr_number=42, target_branch="main", cwd=".", repo="")

    assert watcher.wait_calls[-1]["repo"] == "owner/repo"


@pytest.mark.anyio
async def test_explicit_repo_skips_subprocess(tool_ctx):
    watcher = InMemoryMergeQueueWatcher(
        wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx.merge_queue_watcher = watcher

    with patch(
        "autoskillit.execution.remote_resolver.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        await wait_for_merge_queue(
            pr_number=42,
            target_branch="main",
            cwd=".",
            repo="owner/explicit-repo",
        )

    mock_proc.assert_not_called()
    assert watcher.wait_calls[-1]["repo"] == "owner/explicit-repo"


@pytest.mark.anyio
async def test_watcher_none_returns_error(tool_ctx):
    tool_ctx.merge_queue_watcher = None
    result = json.loads(await wait_for_merge_queue(pr_number=42, target_branch="main", cwd="."))
    assert result["success"] is False
    assert "pr_state" in result
    assert result["pr_state"] == "error"


# ---------------------------------------------------------------------------
# wait_for_ci: remote_url parameter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_parses_remote_url_to_resolve_repo(tool_ctx):
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
    tool_ctx.ci_watcher = watcher

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
async def test_wait_for_ci_remote_url_wins_over_empty_repo(tool_ctx):
    """remote_url= supersedes repo='' — hint priority in resolve_remote_repo."""
    watcher = InMemoryCIWatcher(
        wait_result={
            "conclusion": "success",
            "run_id": 1,
            "failed_jobs": [],
            "head_sha": "abc",
        }
    )
    tool_ctx.ci_watcher = watcher
    await wait_for_ci(
        branch="main",
        remote_url="https://github.com/owner/repo.git",
        repo="",  # empty — remote_url must win
        cwd="/any/cwd",
    )
    assert watcher.wait_calls[-1].get("repo") == "owner/repo"


# ---------------------------------------------------------------------------
# wait_for_merge_queue: remote_url parameter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_merge_queue_parses_remote_url_to_resolve_repo(tool_ctx):
    """When remote_url is provided, wait_for_merge_queue parses it to owner/repo
    without calling any subprocess."""
    watcher = InMemoryMergeQueueWatcher(
        wait_result={"success": True, "pr_state": "merged", "pr_number": 42}
    )
    tool_ctx.merge_queue_watcher = watcher

    result = json.loads(
        await wait_for_merge_queue(
            pr_number=42,
            target_branch="main",
            remote_url="https://github.com/owner/repo.git",
            cwd="/any/cwd",
        )
    )
    assert result["pr_state"] == "merged"
    assert watcher.wait_calls[-1].get("repo") == "owner/repo"


class TestWaitForCiTiming:
    """wait_for_ci records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_wait_for_ci_step_name_records_timing(self, tool_ctx):
        watcher = InMemoryCIWatcher(
            wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
        )
        tool_ctx.ci_watcher = watcher
        await wait_for_ci("main", step_name="ci_wait")
        assert any(e["step_name"] == "ci_wait" for e in tool_ctx.timing_log.get_report())

    @pytest.mark.anyio
    async def test_wait_for_ci_empty_step_name_skips_timing(self, tool_ctx):
        watcher = InMemoryCIWatcher(
            wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
        )
        tool_ctx.ci_watcher = watcher
        await wait_for_ci("main")
        assert tool_ctx.timing_log.get_report() == []


class TestWaitForMergeQueueTiming:
    """wait_for_merge_queue records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_wait_for_merge_queue_step_name_records_timing(self, tool_ctx):
        watcher = InMemoryMergeQueueWatcher(
            wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
        )
        tool_ctx.merge_queue_watcher = watcher
        with patch(
            "autoskillit.execution.remote_resolver.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_proc:
            proc_inst = AsyncMock()
            proc_inst.communicate = AsyncMock(
                return_value=(b"https://github.com/owner/repo.git\n", b"")
            )
            proc_inst.returncode = 0
            mock_proc.return_value = proc_inst
            await wait_for_merge_queue(
                pr_number=1, target_branch="main", cwd=".", step_name="mq_wait"
            )
        assert any(e["step_name"] == "mq_wait" for e in tool_ctx.timing_log.get_report())

    @pytest.mark.anyio
    async def test_wait_for_merge_queue_empty_step_name_skips_timing(self, tool_ctx):
        watcher = InMemoryMergeQueueWatcher(
            wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
        )
        tool_ctx.merge_queue_watcher = watcher
        with patch(
            "autoskillit.execution.remote_resolver.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_proc:
            proc_inst = AsyncMock()
            proc_inst.communicate = AsyncMock(
                return_value=(b"https://github.com/owner/repo.git\n", b"")
            )
            proc_inst.returncode = 0
            mock_proc.return_value = proc_inst
            await wait_for_merge_queue(pr_number=1, target_branch="main", cwd=".")
        assert tool_ctx.timing_log.get_report() == []


@pytest.mark.anyio
async def test_wait_for_merge_queue_invalid_remote_url_falls_through_to_inference(
    tool_ctx, tmp_path
):
    """
    remote_url that parses to None (e.g. file://) does NOT short-circuit;
    inference continues via resolve_remote_repo(cwd).
    """
    watcher = InMemoryMergeQueueWatcher(
        wait_result={
            "success": False,
            "pr_state": "error",
            "reason": "Invalid repo format: None",
        }
    )
    tool_ctx.merge_queue_watcher = watcher

    # provide a file:// remote_url — should fall through, eventually fail gracefully
    result = json.loads(
        await wait_for_merge_queue(
            pr_number=1,
            target_branch="main",
            remote_url="file:///tmp/clone",
            cwd=str(tmp_path),  # real dir, no GitHub remotes
        )
    )
    assert result["pr_state"] == "error"
    # The file:// URL must not resolve to a GitHub repo, so watcher receives repo=None
    assert watcher.wait_calls[-1].get("repo") is None


# ---------------------------------------------------------------------------
# MCP handler — workflow passed via scope (moved from execution/test_ci_params)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_handler_passes_workflow(tool_ctx):
    """wait_for_ci MCP handler must forward workflow to watcher via scope."""
    watcher = InMemoryCIWatcher(
        wait_result={"conclusion": "success", "failed_jobs": [], "run_id": 1}
    )
    tool_ctx.ci_watcher = watcher

    # cwd="" → head_sha inference skipped (empty string is falsy)
    json.loads(await wait_for_ci(branch="main", workflow="tests.yml", cwd=""))

    assert len(watcher.wait_calls) == 1
    assert watcher.wait_calls[-1]["scope"].workflow == "tests.yml"


@pytest.mark.anyio
async def test_get_ci_status_handler_passes_workflow(tool_ctx):
    """get_ci_status MCP handler must forward workflow to watcher via scope."""
    watcher = InMemoryCIWatcher(status_result={"runs": []})
    tool_ctx.ci_watcher = watcher

    await get_ci_status(branch="main", workflow="tests.yml")

    assert watcher.status_calls[-1]["scope"].workflow == "tests.yml"


# ---------------------------------------------------------------------------
# C9-1: exception boundary — bare raise replaced with structured JSON return
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_watcher_exception_returns_structured_json(tool_ctx):
    """wait_for_ci returns structured JSON with conclusion='error' when watcher.wait() raises.

    BEFORE fix: bare raise propagates to track_response_size which adds
    subtype='tool_exception'. AFTER fix: explicit return gives clean JSON.
    """
    watcher = InMemoryCIWatcher()
    watcher.wait_side_effect = RuntimeError("network timeout")
    tool_ctx.ci_watcher = watcher

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


@pytest.mark.anyio
async def test_wait_for_merge_queue_watcher_exception_returns_structured_json(tool_ctx):
    """wait_for_merge_queue returns {success: false, error: ...} when watcher.wait() raises.

    BEFORE fix: bare raise propagates to track_response_size decorator.
    AFTER fix: explicit return gives clean JSON.
    """
    watcher = InMemoryMergeQueueWatcher()
    watcher.wait_side_effect = RuntimeError("connection refused")
    tool_ctx.merge_queue_watcher = watcher

    with patch(
        "autoskillit.execution.remote_resolver.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(return_value=(b"", b""))
        proc_inst.returncode = 1
        mock_proc.return_value = proc_inst

        result = json.loads(
            await wait_for_merge_queue(pr_number=42, target_branch="main", cwd=".")
        )

    assert result["success"] is False
    assert "connection refused" in result["error"]
    assert "subtype" not in result


# ---------------------------------------------------------------------------
# wait_for_ci head_sha enrichment (Gap 5)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_includes_head_sha_in_result(tool_ctx):
    """wait_for_ci result includes head_sha when git rev-parse HEAD succeeds."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx.ci_watcher = watcher
    tool_ctx.runner.push(
        SubprocessResult(
            returncode=0,
            stdout="deadbeef1234\n",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
    )

    result = json.loads(await wait_for_ci("main", cwd="/some/repo"))

    assert result["head_sha"] == "deadbeef1234"


@pytest.mark.anyio
async def test_wait_for_ci_omits_head_sha_when_git_fails(tool_ctx):
    """wait_for_ci result omits head_sha when git rev-parse fails."""
    watcher = InMemoryCIWatcher(
        wait_result={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx.ci_watcher = watcher
    tool_ctx.runner.push(
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
# T10: MCP round-trip exhaustiveness — parametrized over list(PRState)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("pr_state", list(PRState))
async def test_wait_for_merge_queue_serializes_every_pr_state(pr_state, tool_ctx):
    """Every PRState value round-trips faithfully through the MCP handler.

    Adding a new PRState member without a handler test fails this parametrized suite.
    """
    watcher = InMemoryMergeQueueWatcher(
        wait_result={
            "success": pr_state == PRState.MERGED,
            "pr_state": pr_state.value,
            "reason": f"test reason for {pr_state.value}",
        }
    )
    tool_ctx.merge_queue_watcher = watcher

    with patch(
        "autoskillit.execution.remote_resolver.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(
            return_value=(b"https://github.com/owner/repo.git\n", b"")
        )
        proc_inst.returncode = 0
        mock_proc.return_value = proc_inst

        result = json.loads(await wait_for_merge_queue(pr_number=1, target_branch="main", cwd="."))

    assert result["pr_state"] == pr_state.value, (
        f"Expected pr_state={pr_state.value!r} in response, got: {result.get('pr_state')!r}"
    )
    expected_success = pr_state == PRState.MERGED
    assert result["success"] == expected_success, (
        f"Expected success={expected_success!r} for pr_state={pr_state.value!r}, "
        f"got: {result.get('success')!r}"
    )


def test_pr_state_docstring_documents_all_members():
    """T10: wait_for_merge_queue docstring must name every PRState member value.

    Prevents silent docstring drift when new PRState members are added.
    """
    doc = wait_for_merge_queue.__doc__ or ""
    for state in PRState:
        assert state.value in doc, (
            f"PRState.{state.name} ({state.value!r}) is not documented in the "
            f"wait_for_merge_queue docstring. Update the Returns section to include it."
        )


# ---------------------------------------------------------------------------
# check_repo_merge_state: token_factory and http_status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_repo_merge_state_uses_token_factory(tool_ctx, monkeypatch):
    """check_repo_merge_state calls token_factory() when set, not config.github.token."""
    resolved_calls = []

    def factory():
        resolved_calls.append(1)
        return "factory-token"

    tool_ctx.token_factory = factory
    tool_ctx.config.github.token = "config-token"

    captured_tokens = []

    async def fake_fetch(owner, repo, branch, token):
        captured_tokens.append(token)
        return {
            "queue_available": False,
            "merge_group_trigger": False,
            "auto_merge_available": False,
            "ci_event": None,
        }

    monkeypatch.setattr("autoskillit.server.tools_ci.fetch_repo_merge_state", fake_fetch)
    monkeypatch.setattr(
        "autoskillit.server.tools_ci.infer_repo_from_remote",
        AsyncMock(return_value="owner/repo"),
    )

    await check_repo_merge_state(branch="main")
    assert captured_tokens == ["factory-token"]
    assert resolved_calls == [1]


@pytest.mark.anyio
async def test_check_repo_merge_state_falls_back_to_config_token_when_no_factory(
    tool_ctx, monkeypatch
):
    """When token_factory is None, config.github.token is used."""
    tool_ctx.token_factory = None
    tool_ctx.config.github.token = "config-token"

    captured_tokens = []

    async def fake_fetch(owner, repo, branch, token):
        captured_tokens.append(token)
        return {
            "queue_available": False,
            "merge_group_trigger": False,
            "auto_merge_available": False,
            "ci_event": None,
        }

    monkeypatch.setattr("autoskillit.server.tools_ci.fetch_repo_merge_state", fake_fetch)
    monkeypatch.setattr(
        "autoskillit.server.tools_ci.infer_repo_from_remote",
        AsyncMock(return_value="owner/repo"),
    )

    await check_repo_merge_state(branch="main")
    assert captured_tokens == ["config-token"]


@pytest.mark.anyio
async def test_check_repo_merge_state_error_includes_http_status(tool_ctx, monkeypatch):
    """HTTP error response envelope contains http_status field."""

    async def fake_fetch(owner, repo, branch, token):
        response = httpx.Response(
            403,
            request=httpx.Request("POST", "https://api.github.com/graphql"),
        )
        raise httpx.HTTPStatusError("403 Forbidden", request=response.request, response=response)

    monkeypatch.setattr("autoskillit.server.tools_ci.fetch_repo_merge_state", fake_fetch)
    monkeypatch.setattr(
        "autoskillit.server.tools_ci.infer_repo_from_remote",
        AsyncMock(return_value="owner/repo"),
    )

    result = json.loads(await check_repo_merge_state(branch="main"))
    assert "http_status" in result


# ---------------------------------------------------------------------------
# C9-2: exception path shape — conclusion key required for recipe on_result routing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_exception_returns_conclusion_key(tool_ctx, monkeypatch):
    """Inner exception path must return conclusion='error' for recipe on_result routing."""

    async def _exploding_wait(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool_ctx.ci_watcher, "wait", _exploding_wait)
    raw = await wait_for_ci(branch="main", cwd="/tmp")
    result = json.loads(raw)
    assert "conclusion" in result, "Exception path must include conclusion key"
    assert result["conclusion"] == "error"
    assert "error" in result


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
