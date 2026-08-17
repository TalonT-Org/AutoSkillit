"""Tests for the wait_for_merge_queue MCP tool (delegation, repo inference,
remote_url, exception handling, PRState round-trip, timing).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import autoskillit.server.tools.tools_ci_merge_queue as tools_ci_merge_queue
from autoskillit.core import PRState
from autoskillit.server.tools.tools_ci_merge_queue import wait_for_merge_queue
from tests.fakes import InMemoryMergeQueueWatcher
from tests.server._recipe_segment_test_helpers import install_prepared_recipe_segment
from tests.server.conftest import assert_no_timing, assert_step_timed

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_delegates_to_merge_queue_watcher(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
):
    watcher = InMemoryMergeQueueWatcher(
        wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx_kitchen_open.merge_queue_watcher = watcher
    install_prepared_recipe_segment(monkeypatch, tools_ci_merge_queue, step_name="queue")

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
            await wait_for_merge_queue(
                pr_number=42,
                target_branch="develop",
                cwd=".",
                step_name="queue",
            )
        )

    assert result["pr_state"] == "merged"
    assert len(watcher.wait_calls) == 1
    assert watcher.wait_calls[-1]["pr_number"] == 42
    assert watcher.wait_calls[-1]["target_branch"] == "develop"
    assert result["recipe_segment"]["kind"] == "success"


@pytest.mark.anyio
async def test_infers_repo_from_git_remote_when_repo_empty(tool_ctx_kitchen_open):
    watcher = InMemoryMergeQueueWatcher(
        wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx_kitchen_open.merge_queue_watcher = watcher

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
async def test_explicit_repo_skips_subprocess(tool_ctx_kitchen_open):
    watcher = InMemoryMergeQueueWatcher(
        wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
    )
    tool_ctx_kitchen_open.merge_queue_watcher = watcher

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
async def test_watcher_none_returns_error(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
):
    tool_ctx_kitchen_open.merge_queue_watcher = None
    install_prepared_recipe_segment(monkeypatch, tools_ci_merge_queue, step_name="queue")
    result = json.loads(
        await wait_for_merge_queue(
            pr_number=42,
            target_branch="main",
            cwd=".",
            step_name="queue",
        )
    )
    assert result["success"] is False
    assert "pr_state" in result
    assert result["pr_state"] == "error"
    assert result["recipe_segment"]["kind"] == "recovery"


# ---------------------------------------------------------------------------
# wait_for_merge_queue: remote_url parameter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_merge_queue_parses_remote_url_to_resolve_repo(tool_ctx_kitchen_open):
    """When remote_url is provided, wait_for_merge_queue parses it to owner/repo
    without calling any subprocess."""
    watcher = InMemoryMergeQueueWatcher(
        wait_result={"success": True, "pr_state": "merged", "pr_number": 42}
    )
    tool_ctx_kitchen_open.merge_queue_watcher = watcher

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


@pytest.mark.anyio
async def test_wait_for_merge_queue_invalid_remote_url_falls_through_to_inference(
    tool_ctx_kitchen_open, tmp_path
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
    tool_ctx_kitchen_open.merge_queue_watcher = watcher

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
# C9-1: exception boundary — bare raise replaced with structured JSON return
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_merge_queue_watcher_exception_returns_structured_json(
    tool_ctx_kitchen_open,
):
    """wait_for_merge_queue returns {success: false, error: ...} when watcher.wait() raises.

    BEFORE fix: bare raise propagates to track_response_size decorator.
    AFTER fix: explicit return gives clean JSON.
    """
    watcher = InMemoryMergeQueueWatcher()
    watcher.wait_side_effect = RuntimeError("connection refused")
    tool_ctx_kitchen_open.merge_queue_watcher = watcher

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
# T10: MCP round-trip exhaustiveness — parametrized over list(PRState)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("pr_state", list(PRState))
async def test_wait_for_merge_queue_serializes_every_pr_state(pr_state, tool_ctx_kitchen_open):
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
    tool_ctx_kitchen_open.merge_queue_watcher = watcher

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


class TestWaitForMergeQueueTiming:
    """wait_for_merge_queue records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_wait_for_merge_queue_step_name_records_timing(self, tool_ctx_kitchen_open):
        watcher = InMemoryMergeQueueWatcher(
            wait_result={"success": True, "pr_state": "merged", "reason": "PR merged"}
        )
        tool_ctx_kitchen_open.merge_queue_watcher = watcher
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
        assert_step_timed(tool_ctx_kitchen_open.timing_log, "mq_wait")

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
        assert_no_timing(tool_ctx.timing_log)
