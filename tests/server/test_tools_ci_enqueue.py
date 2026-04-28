"""Tests for enqueue_pr MCP tool handler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.fakes import InMemoryMergeQueueWatcher

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_enqueue_pr_delegates_to_watcher_enqueue(tool_ctx):
    """enqueue_pr tool passes auto_merge_available to watcher.enqueue()."""
    watcher = InMemoryMergeQueueWatcher(
        enqueue_result={
            "success": True,
            "pr_number": 42,
            "enrollment_method": "direct_enqueue",
        },
    )
    tool_ctx.merge_queue_watcher = watcher

    with patch(
        "autoskillit.server.tools_ci_merge_queue.resolve_repo_from_remote",
        new_callable=AsyncMock,
        return_value="owner/repo",
    ):
        from autoskillit.server.tools_ci_merge_queue import enqueue_pr

        raw = await enqueue_pr(
            pr_number=42,
            target_branch="main",
            cwd="/tmp/work",
            auto_merge_available=False,
        )
    result = json.loads(raw)
    assert result["success"] is True
    assert result["enrollment_method"] == "direct_enqueue"
    assert len(watcher.enqueue_calls) == 1
    call = watcher.enqueue_calls[0]
    assert call["auto_merge_available"] is False


@pytest.mark.anyio
async def test_enqueue_pr_returns_structured_error_when_watcher_none(tool_ctx):
    """enqueue_pr returns {"success": false} when merge_queue_watcher is None."""
    tool_ctx.merge_queue_watcher = None

    from autoskillit.server.tools_ci_merge_queue import enqueue_pr

    raw = await enqueue_pr(
        pr_number=42,
        target_branch="main",
        cwd="/tmp/work",
        auto_merge_available=True,
    )
    result = json.loads(raw)
    assert result["success"] is False
    assert "error" in result
