"""Tests for release_issue fail_label path and fail label cleanup on claim/release."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from autoskillit.server.tools_issue_lifecycle import claim_issue, release_issue

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestReleaseIssueFailLabel:
    @pytest.mark.anyio
    async def test_release_issue_fail_label_swap(self, tool_ctx, monkeypatch):
        """fail_label swaps in-progress for fail label."""
        mock_client = AsyncMock()
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.swap_labels.return_value = {"success": True, "labels": ["fail"]}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                fail_label="fail",
            )
        )

        assert result["success"] is True
        assert result["failed"] is True
        assert result["fail_label"] == "fail"
        mock_client.ensure_label.assert_called_once_with("owner", "repo", "fail", color="d73a4a")
        mock_client.swap_labels.assert_called_once_with(
            "owner",
            "repo",
            42,
            remove_labels=["in-progress"],
            add_labels=["fail"],
        )

    @pytest.mark.anyio
    async def test_release_issue_success_removes_fail_label(self, tool_ctx, monkeypatch):
        """Staging path includes fail label in remove_labels for cleanup."""
        mock_client = AsyncMock()
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.swap_labels.return_value = {"success": True, "labels": ["staged"]}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="develop",
            )
        )

        assert result["success"] is True
        assert result["staged"] is True
        swap_call = mock_client.swap_labels.call_args
        assert "in-progress" in swap_call.kwargs["remove_labels"]
        assert "fail" in swap_call.kwargs["remove_labels"]
        assert "staged" in swap_call.kwargs["add_labels"]

    @pytest.mark.anyio
    async def test_release_issue_simple_remove_cleans_fail_label(self, tool_ctx, monkeypatch):
        """Simple release (no staging, no fail_label) also removes fail label."""
        mock_client = AsyncMock()
        mock_client.swap_labels.return_value = {"success": True, "labels": []}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
            )
        )

        assert result["success"] is True
        swap_call = mock_client.swap_labels.call_args
        assert "in-progress" in swap_call.kwargs["remove_labels"]
        assert "fail" in swap_call.kwargs["remove_labels"]


class TestClaimIssueFailLabelCleanup:
    @pytest.mark.anyio
    async def test_claim_issue_removes_fail_label_on_claim(self, tool_ctx, monkeypatch):
        """claim_issue uses swap_labels to remove fail label while adding in-progress."""
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "labels": [{"name": "bug"}],
        }
        mock_client.ensure_label.return_value = {"success": True, "created": False}
        mock_client.swap_labels.return_value = {
            "success": True,
            "labels": ["in-progress"],
        }
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        result = json.loads(
            await claim_issue(
                issue_url="https://github.com/owner/repo/issues/42",
            )
        )

        assert result["success"] is True
        assert result["claimed"] is True
        mock_client.swap_labels.assert_called_once_with(
            "owner",
            "repo",
            42,
            remove_labels=["fail"],
            add_labels=["in-progress"],
        )
        mock_client.add_labels.assert_not_called()
