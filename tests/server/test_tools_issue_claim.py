"""Tests for the claim_issue MCP tool (split from test_tools_integrations.py per issue #4796)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.server.tools.tools_issue_labels import claim_issue

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_CLAIM_HELPERS = "autoskillit.fleet"


class TestClaimIssueTool:
    def test_claim_issue_is_gated(self):
        from autoskillit.pipeline.gate import GATED_TOOLS

        assert "claim_issue" in GATED_TOOLS

    @pytest.mark.anyio
    async def test_claim_issue_returns_error_without_github_client(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.github_client = None
        result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_claim_issue_success(self, tool_ctx_kitchen_open):
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "state": "open",
            "labels": [],
            "body": "",
        }
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {"success": True, "labels": ["in-progress"]}
        tool_ctx_kitchen_open.github_client = mock_client
        result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["claimed"] is True
        assert result["issue_number"] == 42

    @pytest.mark.anyio
    async def test_claim_issue_already_claimed(self, tool_ctx_kitchen_open):
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "state": "open",
            "labels": [{"name": "in-progress"}],
            "body": "",
        }
        tool_ctx_kitchen_open.github_client = mock_client
        with patch(f"{_CLAIM_HELPERS}.find_dispatch_for_issue", return_value=None):
            result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["claimed"] is False

    # P5F4-T1
    @pytest.mark.anyio
    async def test_claim_issue_binds_structlog_context(self, tool_ctx_kitchen_open, monkeypatch):
        """claim_issue must scope structlog context vars via bound_contextvars."""
        from contextlib import contextmanager

        import structlog

        captured = {}

        @contextmanager
        def fake_bound_contextvars(**kwargs):
            captured.update(kwargs)
            yield

        monkeypatch.setattr(structlog.contextvars, "bound_contextvars", fake_bound_contextvars)

        tool_ctx_kitchen_open.github_client = None  # triggers early return inside with block

        await claim_issue(issue_url="https://github.com/owner/repo/issues/1")
        assert captured == {
            "tool": "claim_issue",
            "issue_url": "https://github.com/owner/repo/issues/1",
        }

    @pytest.mark.anyio
    async def test_contextvars_cleaned_after_tool_return(self, tool_ctx_kitchen_open):
        """bound_contextvars restores context after tool returns — no leakage."""
        import structlog

        structlog.contextvars.clear_contextvars()
        tool_ctx_kitchen_open.github_client = None  # triggers early return

        await claim_issue(issue_url="https://github.com/owner/repo/issues/1")

        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            structlog.get_logger().info("probe")
        assert "tool" not in logs[0], "tool contextvar leaked past tool function boundary"

    @pytest.mark.anyio
    async def test_claim_issue_allow_reentry_true_returns_claimed_true_when_already_labeled(
        self, tool_ctx_kitchen_open
    ):
        """allow_reentry=True with label present: returns claimed=True and reentry=True."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "state": "open",
            "labels": [{"name": "in-progress"}],
            "body": "",
        }
        tool_ctx_kitchen_open.github_client = mock_client
        result = json.loads(
            await claim_issue("https://github.com/owner/repo/issues/42", allow_reentry=True)
        )
        assert result["success"] is True
        assert result["claimed"] is True
        assert result["reentry"] is True
        mock_client.add_labels.assert_not_called()  # no re-application needed

    @pytest.mark.anyio
    async def test_claim_issue_allow_reentry_false_returns_claimed_false_when_already_labeled(
        self, tool_ctx_kitchen_open
    ):
        """Default allow_reentry=False: claimed=False when label present and no dispatch found."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "state": "open",
            "labels": [{"name": "in-progress"}],
            "body": "",
        }
        tool_ctx_kitchen_open.github_client = mock_client
        with patch(f"{_CLAIM_HELPERS}.find_dispatch_for_issue", return_value=None):
            result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["claimed"] is False
        assert "reentry" not in result

    @pytest.mark.anyio
    async def test_claim_issue_allow_reentry_true_still_claims_when_label_absent(
        self, tool_ctx_kitchen_open
    ):
        """allow_reentry=True with no pre-existing label performs normal claim."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "state": "open",
            "labels": [],
            "body": "",
        }
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.swap_labels.return_value = {"success": True, "labels": ["in-progress"]}
        tool_ctx_kitchen_open.github_client = mock_client
        result = json.loads(
            await claim_issue("https://github.com/owner/repo/issues/42", allow_reentry=True)
        )
        assert result["success"] is True
        assert result["claimed"] is True
        assert result.get("reentry", False) is False
        call_kwargs = mock_client.swap_labels.call_args.kwargs
        assert set(call_kwargs["remove_labels"]) == {"queued", "fail"}
        assert call_kwargs["add_labels"] == ["in-progress"]

    @pytest.mark.anyio
    async def test_claim_issue_with_queued_label(self, tool_ctx_kitchen_open):
        """claim_issue with label=queued uses registry color/description and removes fail."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "state": "open",
            "labels": [],
            "body": "",
        }
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.swap_labels.return_value = {"success": True, "labels": ["queued"]}
        tool_ctx_kitchen_open.github_client = mock_client
        result = json.loads(
            await claim_issue(issue_url="https://github.com/owner/repo/issues/1", label="queued")
        )
        assert result["success"] is True
        assert result["claimed"] is True
        mock_client.ensure_label.assert_called_once_with(
            "owner",
            "repo",
            "queued",
            color="c2e0c6",
            description="Issue claimed by orchestrator, waiting for recipe pickup",
        )
        call_kwargs = mock_client.swap_labels.call_args.kwargs
        assert set(call_kwargs["remove_labels"]) == {"fail"}
        assert call_kwargs["add_labels"] == ["queued"]

    @pytest.mark.anyio
    async def test_claim_issue_default_removes_queued_and_fail(self, tool_ctx_kitchen_open):
        """claim_issue with default label removes both queued and fail labels."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "state": "open",
            "labels": [],
            "body": "",
        }
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.swap_labels.return_value = {"success": True, "labels": ["in-progress"]}
        tool_ctx_kitchen_open.github_client = mock_client
        result = json.loads(await claim_issue(issue_url="https://github.com/owner/repo/issues/1"))
        assert result["success"] is True
        assert result["claimed"] is True
        mock_client.ensure_label.assert_called_once_with(
            "owner",
            "repo",
            "in-progress",
            color="fbca04",
            description="Issue is actively being processed by a pipeline session",
        )
        call_kwargs = mock_client.swap_labels.call_args.kwargs
        assert set(call_kwargs["remove_labels"]) >= {"queued", "fail"}
        assert call_kwargs["add_labels"] == ["in-progress"]
