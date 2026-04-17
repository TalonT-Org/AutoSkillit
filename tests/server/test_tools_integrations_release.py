"""Tests for release_issue staged lifecycle behaviour."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.server.tools_issue_lifecycle import release_issue

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestReleaseIssueStagedLifecycle:
    @pytest.mark.anyio
    async def test_release_issue_non_default_branch_applies_staged(self, tool_ctx, monkeypatch):
        """Non-default target_branch removes in-progress and applies staged label."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {"success": True, "labels": ["staged"]}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
            )
        )
        assert result["success"] is True
        assert result["staged"] is True
        assert result["staged_label"] == "staged"
        mock_client.remove_label.assert_called_once_with("owner", "repo", 42, "in-progress")
        mock_client.ensure_label.assert_called_once_with(
            "owner",
            "repo",
            "staged",
            color="0075ca",
            description="Implementation staged and waiting for promotion to main",
        )
        mock_client.add_labels.assert_called_once_with("owner", "repo", 42, ["staged"])

    @pytest.mark.anyio
    async def test_release_issue_default_branch_no_staged(self, tool_ctx, monkeypatch):
        """release_issue with target_branch='main' only removes in-progress."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="main",
            )
        )
        assert result["success"] is True
        assert result.get("staged") is False
        mock_client.remove_label.assert_called_once_with("owner", "repo", 42, "in-progress")
        mock_client.ensure_label.assert_not_called()
        mock_client.add_labels.assert_not_called()

    @pytest.mark.anyio
    async def test_release_issue_no_target_branch_no_staged(self, tool_ctx, monkeypatch):
        """release_issue without target_branch: current behavior, no staged label."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
            )
        )
        assert result["success"] is True
        assert result.get("staged") is False

    @pytest.mark.anyio
    async def test_release_issue_staged_label_idempotent(self, tool_ctx, monkeypatch):
        """ensure_label treats 422 (already exists) as success — applies it without error."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": False}  # 422 path
        mock_client.add_labels.return_value = {"success": True, "labels": ["staged"]}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
            )
        )
        assert result["success"] is True
        assert result["staged"] is True

    @pytest.mark.anyio
    async def test_release_issue_custom_staged_label(self, tool_ctx, monkeypatch):
        """staged_label parameter overrides the default 'staged' label name."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {
            "success": True,
            "labels": ["awaiting-promotion"],
        }
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
                staged_label="awaiting-promotion",
            )
        )
        assert result["success"] is True
        assert result["staged"] is True
        assert result["staged_label"] == "awaiting-promotion"
        mock_client.ensure_label.assert_called_once_with(
            "owner",
            "repo",
            "awaiting-promotion",
            color="0075ca",
            description="Implementation staged and waiting for promotion to main",
        )

    @pytest.mark.anyio
    async def test_release_issue_ensure_label_failure_returns_error(self, tool_ctx, monkeypatch):
        """When ensure_label fails, release_issue returns an error without applying the label."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": False, "error": "API error"}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
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
    async def test_release_issue_add_labels_failure_returns_error(self, tool_ctx, monkeypatch):
        """When add_labels fails after ensure_label, release_issue returns an error."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.add_labels.return_value = {"success": False, "error": "Labels limit"}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="integration",
            )
        )
        assert result["success"] is False
        assert "staged label" in result["error"]

    @pytest.mark.anyio
    async def test_release_issue_staged_null_when_not_staged(self, tool_ctx, monkeypatch):
        """staged_label field is None in response when staging was not applied."""
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch="main",
            )
        )
        assert result["staged_label"] is None

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "default_base_branch,promotion_target,target_branch,expected_staged",
        [
            # production scenario: default_base_branch overridden to integration for routing
            ("integration", "main", "integration", True),
            # integration explicitly set as promotion_target: landing there = done
            ("integration", "integration", "integration", False),
            # non-default target against main promotion target
            ("main", "main", "integration", True),
            # promotion_target overridden to something other than main
            ("main", "stable", "stable", False),
            ("main", "stable", "integration", True),
        ],
    )
    async def test_release_issue_staging_uses_promotion_target(
        self,
        tool_ctx,
        monkeypatch,
        default_base_branch,
        promotion_target,
        target_branch,
        expected_staged,
    ):
        """Regression: staging comparison uses promotion_target, not default_base_branch.

        When default_base_branch == target_branch (e.g. both "integration"),
        staged label must still be applied if promotion_target != target_branch.
        Conversely, no staged label when target_branch == promotion_target, regardless
        of default_base_branch.
        """
        tool_ctx.config.branching.default_base_branch = default_base_branch
        tool_ctx.config.branching.promotion_target = promotion_target
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        mock_client.ensure_label.return_value = {"success": True, "created": False}
        mock_client.add_labels.return_value = {
            "success": True,
            "labels": [tool_ctx.config.github.staged_label],
        }
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/42",
                target_branch=target_branch,
            )
        )
        assert result["staged"] is expected_staged, (
            f"staged must be {expected_staged} when "
            f"target_branch={target_branch!r}, promotion_target={promotion_target!r}, "
            f"default_base_branch={default_base_branch!r}"
        )
        if expected_staged:
            assert result.get("staged_label") is not None, (
                f"staged_label must not be None when staged=True "
                f"(target_branch={target_branch!r}, promotion_target={promotion_target!r})"
            )
        else:
            assert result.get("staged_label") is None, (
                f"staged_label must be None when staged=False "
                f"(target_branch={target_branch!r}, promotion_target={promotion_target!r})"
            )
