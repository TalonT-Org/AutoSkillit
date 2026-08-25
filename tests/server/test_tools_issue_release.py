"""Tests for the release_issue MCP tool (split from test_tools_integrations.py per issue #4796)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import autoskillit.server.tools.tools_issue_labels as tools_issue_labels
from autoskillit.server.tools.tools_issue_labels import release_issue
from tests.server._recipe_segment_test_helpers import (
    assert_recovery_recipe_segment,
    install_prepared_recipe_segment,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestReleaseIssueTool:
    def test_release_issue_is_gated(self):
        from autoskillit.pipeline.gate import GATED_TOOLS

        assert "release_issue" in GATED_TOOLS

    @pytest.mark.anyio
    async def test_release_issue_returns_error_without_github_client(
        self,
        tool_ctx_kitchen_open,
        monkeypatch: pytest.MonkeyPatch,
    ):
        tool_ctx_kitchen_open.github_client = None
        install_prepared_recipe_segment(monkeypatch, tools_issue_labels, step_name="release")
        result = json.loads(
            await release_issue(
                "https://github.com/owner/repo/issues/42",
                step_name="release",
            )
        )
        assert result["success"] is False
        assert "error" in result
        assert_recovery_recipe_segment(result, step_name="release")

    @pytest.mark.anyio
    async def test_release_issue_success_returns_recovery_segment(
        self,
        tool_ctx_kitchen_open,
        monkeypatch: pytest.MonkeyPatch,
    ):
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        tool_ctx_kitchen_open.github_client = mock_client
        install_prepared_recipe_segment(monkeypatch, tools_issue_labels, step_name="release")
        result = json.loads(
            await release_issue(
                "https://github.com/owner/repo/issues/42",
                step_name="release",
            )
        )
        assert result["success"] is True
        assert result["issue_number"] == 42
        assert_recovery_recipe_segment(result, step_name="release")

    # P5F4-T2
    @pytest.mark.anyio
    async def test_release_issue_binds_structlog_context(self, tool_ctx_kitchen_open, monkeypatch):
        """release_issue must scope structlog context vars via bound_contextvars."""
        from contextlib import contextmanager

        import structlog

        captured = {}

        @contextmanager
        def fake_bound_contextvars(**kwargs):
            captured.update(kwargs)
            yield

        monkeypatch.setattr(structlog.contextvars, "bound_contextvars", fake_bound_contextvars)

        tool_ctx_kitchen_open.github_client = None  # triggers early return inside with block

        await release_issue(issue_url="https://github.com/owner/repo/issues/1")
        assert captured == {
            "tool": "release_issue",
            "issue_url": "https://github.com/owner/repo/issues/1",
        }
