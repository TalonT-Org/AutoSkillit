"""Tests for the get_ci_status MCP tool (no branch/run_id argument, no watcher,
workflow propagation).
"""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_ci_watch import get_ci_status
from tests.fakes import InMemoryCIWatcher

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_get_ci_status_missing_branch_and_run_id(tool_ctx_kitchen_open):
    watcher = InMemoryCIWatcher()
    tool_ctx_kitchen_open.ci_watcher = watcher

    result = json.loads(await get_ci_status())
    assert result["runs"] == []
    assert "error" in result


@pytest.mark.anyio
async def test_get_ci_status_no_watcher(tool_ctx_kitchen_open):
    tool_ctx_kitchen_open.ci_watcher = None
    result = json.loads(await get_ci_status(branch="main"))
    assert result["runs"] == []
    assert "not configured" in result["error"]


@pytest.mark.anyio
async def test_get_ci_status_handler_passes_workflow(tool_ctx_kitchen_open):
    """get_ci_status MCP handler must forward workflow to watcher via scope."""
    watcher = InMemoryCIWatcher(status_result={"runs": []})
    tool_ctx_kitchen_open.ci_watcher = watcher

    await get_ci_status(branch="main", workflow="tests.yml")

    assert watcher.status_calls[-1]["scope"].workflow == "tests.yml"
