"""Tests for the wait_for_ci, get_ci_status, and wait_for_merge_queue MCP tool handlers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.tools_ci import get_ci_status, wait_for_ci, wait_for_merge_queue

# ---------------------------------------------------------------------------
# Gate membership
# ---------------------------------------------------------------------------


def test_wait_for_ci_is_gated():
    assert "wait_for_ci" in GATED_TOOLS


def test_get_ci_status_is_ungated():
    assert "get_ci_status" in UNGATED_TOOLS
    assert "get_ci_status" not in GATED_TOOLS


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
    mock_watcher = AsyncMock()
    mock_watcher.wait = AsyncMock(
        return_value={"run_id": 12345, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx.ci_watcher = mock_watcher

    with patch(
        "autoskillit.server.tools_ci.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(return_value=(b"abc123\n", b""))
        proc_inst.returncode = 0
        mock_proc.return_value = proc_inst

        result = json.loads(await wait_for_ci("main", cwd="/some/repo"))

    assert result["run_id"] == 12345
    assert result["conclusion"] == "success"
    assert result["failed_jobs"] == []


@pytest.mark.anyio
async def test_wait_for_ci_failure_response(tool_ctx):
    mock_watcher = AsyncMock()
    mock_watcher.wait = AsyncMock(
        return_value={
            "run_id": 12345,
            "conclusion": "failure",
            "failed_jobs": ["test", "lint"],
        }
    )
    tool_ctx.ci_watcher = mock_watcher

    with patch(
        "autoskillit.server.tools_ci.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(return_value=(b"abc123\n", b""))
        proc_inst.returncode = 0
        mock_proc.return_value = proc_inst

        result = json.loads(await wait_for_ci("main", cwd="/some/repo"))

    assert result["conclusion"] == "failure"
    assert sorted(result["failed_jobs"]) == ["lint", "test"]


# ---------------------------------------------------------------------------
# wait_for_ci head_sha inference
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_infers_head_sha(tool_ctx):
    """When head_sha is not provided, it's inferred via git rev-parse HEAD."""
    mock_watcher = AsyncMock()
    mock_watcher.wait = AsyncMock(
        return_value={"run_id": 1, "conclusion": "success", "failed_jobs": []}
    )
    tool_ctx.ci_watcher = mock_watcher

    with patch(
        "autoskillit.server.tools_ci.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(return_value=(b"abc123\n", b""))
        proc_inst.returncode = 0
        mock_proc.return_value = proc_inst

        await wait_for_ci("main", cwd="/some/repo")

    # Verify that wait was called with the inferred head_sha
    call_kwargs = mock_watcher.wait.call_args
    assert call_kwargs.kwargs.get("head_sha") == "abc123"


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
async def test_get_ci_status_ungated(tool_ctx):
    """get_ci_status works even when gate is closed."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    mock_watcher = AsyncMock()
    mock_watcher.status = AsyncMock(
        return_value={
            "runs": [
                {
                    "id": 100,
                    "status": "completed",
                    "conclusion": "success",
                    "failed_jobs": [],
                }
            ]
        }
    )
    tool_ctx.ci_watcher = mock_watcher

    result = json.loads(await get_ci_status(branch="main", cwd="/repo"))
    assert len(result["runs"]) == 1
    assert result["runs"][0]["conclusion"] == "success"


@pytest.mark.anyio
async def test_get_ci_status_missing_branch_and_run_id(tool_ctx):
    mock_watcher = AsyncMock()
    tool_ctx.ci_watcher = mock_watcher

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
    mock_watcher = AsyncMock()
    mock_watcher.wait = AsyncMock(
        return_value={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx.merge_queue_watcher = mock_watcher

    with patch(
        "autoskillit.server.tools_ci.asyncio.create_subprocess_exec",
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
    mock_watcher.wait.assert_called_once()
    call_kwargs = mock_watcher.wait.call_args
    assert call_kwargs.kwargs["pr_number"] == 42
    assert call_kwargs.kwargs["target_branch"] == "integration"


@pytest.mark.anyio
async def test_infers_repo_from_git_remote_when_repo_empty(tool_ctx):
    mock_watcher = AsyncMock()
    mock_watcher.wait = AsyncMock(
        return_value={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx.merge_queue_watcher = mock_watcher

    with patch(
        "autoskillit.server.tools_ci.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        proc_inst = AsyncMock()
        proc_inst.communicate = AsyncMock(
            return_value=(b"https://github.com/owner/repo.git\n", b"")
        )
        proc_inst.returncode = 0
        mock_proc.return_value = proc_inst

        await wait_for_merge_queue(pr_number=42, target_branch="main", cwd=".", repo="")

    call_kwargs = mock_watcher.wait.call_args
    assert call_kwargs.kwargs["repo"] == "owner/repo"


@pytest.mark.anyio
async def test_explicit_repo_skips_subprocess(tool_ctx):
    mock_watcher = AsyncMock()
    mock_watcher.wait = AsyncMock(
        return_value={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx.merge_queue_watcher = mock_watcher

    with patch(
        "autoskillit.server.tools_ci.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_proc:
        await wait_for_merge_queue(
            pr_number=42,
            target_branch="main",
            cwd=".",
            repo="owner/explicit-repo",
        )

    mock_proc.assert_not_called()
    call_kwargs = mock_watcher.wait.call_args
    assert call_kwargs.kwargs["repo"] == "owner/explicit-repo"


@pytest.mark.anyio
async def test_watcher_none_returns_error(tool_ctx):
    tool_ctx.merge_queue_watcher = None
    result = json.loads(await wait_for_merge_queue(pr_number=42, target_branch="main", cwd="."))
    assert result["success"] is False
    assert "pr_state" in result
    assert result["pr_state"] == "error"
