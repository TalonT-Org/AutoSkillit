"""Tests for release_issue staged lifecycle behaviour."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.server.tools_integrations import release_issue


class TestReleaseIssueStagedLifecycle:
    @pytest.mark.anyio
    async def test_release_issue_non_default_branch_applies_staged(self, tool_ctx):
        """release_issue with target_branch='integration' removes in-progress and applies staged."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {"success": True, "labels": ["staged"]}
        tool_ctx.github_client = mock_client
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
            )
        )
        assert result["success"] is True
        assert result["staged"] is True
        assert result["staged_label"] == "staged"
        mock_client.remove_label.assert_called_once()
        mock_client.ensure_label.assert_called_once()
        mock_client.add_labels.assert_called_once()

    @pytest.mark.anyio
    async def test_release_issue_default_branch_no_staged(self, tool_ctx):
        """release_issue with target_branch='main' only removes in-progress."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        tool_ctx.github_client = mock_client
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="main",
            )
        )
        assert result["success"] is True
        assert result.get("staged") is False
        mock_client.remove_label.assert_called_once()
        mock_client.ensure_label.assert_not_called()
        mock_client.add_labels.assert_not_called()

    @pytest.mark.anyio
    async def test_release_issue_no_target_branch_no_staged(self, tool_ctx):
        """release_issue without target_branch: current behavior, no staged label."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        tool_ctx.github_client = mock_client
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
            )
        )
        assert result["success"] is True
        assert result.get("staged") is False

    @pytest.mark.anyio
    async def test_release_issue_staged_label_idempotent(self, tool_ctx):
        """ensure_label treats 422 (already exists) as success — applies it without error."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": False}  # 422 path
        mock_client.add_labels.return_value = {"success": True, "labels": ["staged"]}
        tool_ctx.github_client = mock_client
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
            )
        )
        assert result["success"] is True
        assert result["staged"] is True

    @pytest.mark.anyio
    async def test_release_issue_custom_staged_label(self, tool_ctx):
        """staged_label parameter overrides the default 'staged' label name."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {
            "success": True,
            "labels": ["awaiting-promotion"],
        }
        tool_ctx.github_client = mock_client
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
                staged_label="awaiting-promotion",
            )
        )
        assert result["staged_label"] == "awaiting-promotion"
        mock_client.ensure_label.assert_called_once_with(
            "owner",
            "repo",
            "awaiting-promotion",
            color="0075ca",
            description="Implementation staged and waiting for promotion to main",
        )

    @pytest.mark.anyio
    async def test_release_issue_ensure_label_failure_returns_error(self, tool_ctx):
        """When ensure_label fails, release_issue returns an error without applying the label."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": False, "error": "API error"}
        tool_ctx.github_client = mock_client
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
            )
        )
        assert result["success"] is False
        assert "staged label" in result["error"]
        mock_client.add_labels.assert_not_called()

    @pytest.mark.anyio
    async def test_release_issue_add_labels_failure_returns_error(self, tool_ctx):
        """When add_labels fails after ensure_label, release_issue returns an error."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {"success": False, "error": "Labels limit"}
        tool_ctx.github_client = mock_client
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
            )
        )
        assert result["success"] is False
        assert "staged label" in result["error"]

    @pytest.mark.anyio
    async def test_release_issue_staged_null_when_not_staged(self, tool_ctx):
        """staged_label field is None in response when staging was not applied."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        tool_ctx.github_client = mock_client
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="main",
            )
        )
        assert result["staged_label"] is None
