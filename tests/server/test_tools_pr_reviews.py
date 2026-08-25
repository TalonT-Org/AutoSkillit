"""get_pr_reviews MCP tool tests (split from test_tools_integrations.py #4796)."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_pr_ops import get_pr_reviews
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestGetPrReviews:
    @pytest.mark.anyio
    async def test_returns_structured_reviews(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0,
                json.dumps(
                    [
                        {"user": {"login": "reviewer1"}, "state": "APPROVED", "body": "LGTM"},
                        {
                            "user": {"login": "reviewer2"},
                            "state": "CHANGES_REQUESTED",
                            "body": "Fix this",
                        },
                    ]
                ),
                "",
            )
        )
        result = json.loads(await get_pr_reviews(42, ".", repo="owner/repo"))
        assert len(result["reviews"]) == 2
        assert result["reviews"][0] == {"author": "reviewer1", "state": "APPROVED", "body": "LGTM"}

    @pytest.mark.anyio
    async def test_empty_reviews(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, json.dumps([]), ""))
        result = json.loads(await get_pr_reviews(42, ".", repo="owner/repo"))
        assert result["reviews"] == []

    @pytest.mark.anyio
    async def test_gh_command_failure_returns_error(self, tool_ctx):
        tool_ctx.runner.push(_make_result(1, "", "could not find PR"))
        result = json.loads(await get_pr_reviews(99, ".", repo="owner/repo"))
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_without_repo_uses_pr_view(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0,
                json.dumps(
                    {
                        "reviews": [
                            {"author": {"login": "x"}, "state": "APPROVED", "body": ""},
                        ]
                    }
                ),
                "",
            )
        )
        result = json.loads(await get_pr_reviews(42, "."))
        assert result["reviews"][0]["author"] == "x"

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await get_pr_reviews(1, "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
