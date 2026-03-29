"""Tests for server/tools_github.py — fetch_github_issue and get_issue_title."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools_github import fetch_github_issue, get_issue_title

# ---------------------------------------------------------------------------
# fetch_github_issue
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_github_issue_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await fetch_github_issue("owner/repo#42"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_fetch_github_issue_no_client(tool_ctx) -> None:
    """github_client=None → {"success": False, "error": "GitHub client not configured"}."""
    tool_ctx.github_client = None
    result = json.loads(await fetch_github_issue("owner/repo#42"))
    assert result["success"] is False
    assert "GitHub client not configured" in result["error"]


@pytest.mark.anyio
async def test_fetch_github_issue_success(tool_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """client.fetch_issue returns data → JSON with that data."""
    issue_data = {
        "success": True,
        "issue_number": 42,
        "title": "Test issue",
        "url": "https://github.com/owner/repo/issues/42",
        "state": "open",
        "labels": [],
        "content": "## Body\nSome content.",
    }
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(await fetch_github_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is True
    assert result["issue_number"] == 42


@pytest.mark.anyio
async def test_fetch_github_issue_bare_number_no_default_repo(tool_ctx) -> None:
    """issue_url='42', no default_repo → error response."""
    tool_ctx.github_client = AsyncMock()
    tool_ctx.config.github.default_repo = ""

    result = json.loads(await fetch_github_issue("42"))
    assert result["success"] is False
    assert "default_repo" in result["error"] or "bare issue number" in result["error"].lower()


@pytest.mark.anyio
async def test_fetch_github_issue_bare_number_with_default_repo(
    tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue_url='42', default_repo='owner/repo' → resolves to 'owner/repo#42'."""
    tool_ctx.config.github.default_repo = "owner/repo"
    issue_data = {
        "success": True,
        "issue_number": 42,
        "title": "From bare number",
        "url": "https://github.com/owner/repo/issues/42",
        "state": "open",
        "labels": [],
        "content": "content",
    }
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(await fetch_github_issue("42"))
    assert result["success"] is True
    # Verify fetch_issue was called with the resolved ref
    call_args = tool_ctx.github_client.fetch_issue.call_args
    assert "owner/repo#42" in call_args.args[0]


# ---------------------------------------------------------------------------
# get_issue_title
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_issue_title_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await get_issue_title("owner/repo#42"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_get_issue_title_no_client(tool_ctx) -> None:
    """github_client=None → error response."""
    tool_ctx.github_client = None
    result = json.loads(await get_issue_title("owner/repo#42"))
    assert result["success"] is False


@pytest.mark.anyio
async def test_get_issue_title_success(tool_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """client.fetch_title returns data → JSON with title and slug."""
    title_data = {
        "success": True,
        "number": 42,
        "title": "Fix the bug",
        "slug": "fix-the-bug",
    }
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.fetch_title = AsyncMock(return_value=title_data)

    result = json.loads(await get_issue_title("owner/repo#42"))
    assert result["success"] is True
    assert result["title"] == "Fix the bug"
