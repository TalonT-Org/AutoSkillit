"""Tests for the bulk_close_issues MCP tool (split from test_tools_integrations.py per issue #4796)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.server.tools.tools_pr_ops import bulk_close_issues
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestBulkCloseIssues:
    @pytest.fixture(autouse=True)
    def _mock_rate_limit_sleep(self, monkeypatch):
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_pr_ops.asyncio.sleep",
            AsyncMock(),
        )

    @pytest.mark.anyio
    async def test_closes_all_issues_successfully(self, tool_ctx_kitchen_open):
        for _ in range(3):
            tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await bulk_close_issues([1, 2, 3], "", "."))
        assert result["closed"] == [1, 2, 3]
        assert result["failed"] == []

    @pytest.mark.anyio
    async def test_partial_failure_tracked_per_issue(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(1, "", "not found"))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await bulk_close_issues([1, 2, 3], "", "."))
        assert result["closed"] == [1, 3]
        assert result["failed"] == [2]

    @pytest.mark.anyio
    async def test_empty_numbers_list(self, tool_ctx_kitchen_open):
        result = json.loads(await bulk_close_issues([], "", "."))
        assert result == {"closed": [], "failed": []}

    @pytest.mark.anyio
    async def test_comment_appended_to_body_when_provided(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "existing body", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await bulk_close_issues([7], "Closed by pipeline.", "."))
        all_cmds = [call[0] for call in tool_ctx_kitchen_open.runner.call_args_list]
        edit_calls = [cmd for cmd in all_cmds if "edit" in cmd]
        assert any("--body-file" in cmd for cmd in edit_calls), (
            "Expected gh issue edit --body-file call"
        )
        assert result["closed"] == [7]

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await bulk_close_issues([1], "", "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
