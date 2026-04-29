"""Tests for label whitelist validation in server tool handlers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.server.tools_issue_lifecycle import claim_issue, prepare_issue, release_issue

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestClaimIssueWhitelist:
    @pytest.mark.anyio
    async def test_claim_issue_rejects_disallowed_label(self, tool_ctx, monkeypatch):
        """claim_issue returns error when effective_label is not in whitelist."""
        tool_ctx.config.github.allowed_labels = ["bug", "enhancement"]
        mock_client = AsyncMock()
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        result = json.loads(
            await claim_issue(
                issue_url="https://github.com/owner/repo/issues/1",
                label="unlisted-label",
            )
        )
        assert result["success"] is False
        assert "unlisted-label" in result["error"]
        mock_client.ensure_label.assert_not_called()
        mock_client.swap_labels.assert_not_called()

    @pytest.mark.anyio
    async def test_claim_issue_allows_whitelisted_label(self, tool_ctx, monkeypatch):
        """claim_issue proceeds when effective_label is in whitelist."""
        tool_ctx.config.github.allowed_labels = ["in-progress", "bug"]
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {
            "success": True,
            "labels": [],
        }
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.swap_labels.return_value = {"success": True, "labels": ["in-progress"]}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        result = json.loads(
            await claim_issue(
                issue_url="https://github.com/owner/repo/issues/1",
                label="in-progress",
            )
        )
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_claim_issue_empty_whitelist_permits_any_label(self, tool_ctx, monkeypatch):
        """claim_issue with empty allowed_labels proceeds without restriction."""
        tool_ctx.config.github.allowed_labels = []
        mock_client = AsyncMock()
        mock_client.fetch_issue.return_value = {"success": True, "labels": []}
        mock_client.ensure_label.return_value = {"success": True, "created": True}
        mock_client.swap_labels.return_value = {"success": True, "labels": ["any-label"]}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        result = json.loads(
            await claim_issue(
                issue_url="https://github.com/owner/repo/issues/1",
                label="any-label",
            )
        )
        assert result["success"] is True


class TestReleaseIssueWhitelist:
    @pytest.mark.anyio
    async def test_release_issue_rejects_disallowed_staged_label(self, tool_ctx, monkeypatch):
        """release_issue returns error when staged label is not in whitelist."""
        tool_ctx.config.github.allowed_labels = ["in-progress", "bug"]
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/1",
                target_branch="integration",
                staged_label="unlisted-staged",
            )
        )
        assert result["success"] is False
        assert "unlisted-staged" in result["error"]
        mock_client.ensure_label.assert_not_called()

    @pytest.mark.anyio
    async def test_release_issue_no_staging_skips_validation(self, tool_ctx, monkeypatch):
        """release_issue without staging doesn't validate staged_label."""
        tool_ctx.config.github.allowed_labels = ["in-progress"]
        mock_client = AsyncMock()
        mock_client.remove_label.return_value = {"success": True}
        monkeypatch.setattr(tool_ctx, "github_client", mock_client)

        # target_branch=main means no staging — staged label not validated
        result = json.loads(
            await release_issue(
                issue_url="https://github.com/owner/repo/issues/1",
                target_branch="main",
            )
        )
        assert result["success"] is True


class TestPrepareIssueWhitelist:
    @pytest.mark.anyio
    async def test_prepare_issue_rejects_disallowed_explicit_label(self, tool_ctx, monkeypatch):
        """prepare_issue returns error when explicit labels contain a disallowed entry."""
        tool_ctx.config.github.allowed_labels = ["bug", "enhancement"]
        mock_executor = AsyncMock()
        tool_ctx.executor = mock_executor

        result = json.loads(
            await prepare_issue(
                title="Test issue",
                body="Test body",
                labels=["arbitrary-label"],
            )
        )
        assert result["success"] is False
        assert "arbitrary-label" in result["error"]
        mock_executor.run.assert_not_called()

    @pytest.mark.anyio
    async def test_prepare_issue_allows_whitelisted_explicit_labels(self, tool_ctx, monkeypatch):
        """prepare_issue proceeds when all explicit labels are whitelisted."""
        import json as _json

        from autoskillit.core import SkillResult
        from autoskillit.core.types import RetryReason
        from autoskillit.server.tools_issue_lifecycle import (
            _PREPARE_RESULT_END,
            _PREPARE_RESULT_START,
        )

        tool_ctx.config.github.allowed_labels = ["bug", "enhancement"]
        mock_executor = AsyncMock()
        payload = _json.dumps({"issue_url": "https://github.com/x/y/issues/1", "route": "impl"})
        mock_executor.run.return_value = SkillResult(
            success=True,
            result=f"{_PREPARE_RESULT_START}\n{payload}\n{_PREPARE_RESULT_END}",
            session_id="sid",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx.executor = mock_executor

        result = json.loads(
            await prepare_issue(
                title="Test issue",
                body="Test body",
                labels=["bug"],
            )
        )
        assert result["success"] is True
        mock_executor.run.assert_awaited_once()

    @pytest.mark.anyio
    async def test_prepare_issue_no_labels_skips_validation(self, tool_ctx, monkeypatch):
        """prepare_issue with labels=None skips whitelist validation entirely."""
        import json as _json

        from autoskillit.core import SkillResult
        from autoskillit.core.types import RetryReason
        from autoskillit.server.tools_issue_lifecycle import (
            _PREPARE_RESULT_END,
            _PREPARE_RESULT_START,
        )

        tool_ctx.config.github.allowed_labels = ["bug"]  # restrictive whitelist
        mock_executor = AsyncMock()
        payload = _json.dumps({"issue_url": "https://github.com/x/y/issues/1", "route": "impl"})
        mock_executor.run.return_value = SkillResult(
            success=True,
            result=f"{_PREPARE_RESULT_START}\n{payload}\n{_PREPARE_RESULT_END}",
            session_id="sid",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        tool_ctx.executor = mock_executor

        result = json.loads(
            await prepare_issue(
                title="Test issue",
                body="Test body",
                labels=None,
            )
        )
        assert result["success"] is True
