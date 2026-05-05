"""Tests for server/tools_github.py — fetch_github_issue and get_issue_title."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.tools.tools_github import (
    fetch_github_issue,
    get_issue_title,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

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
async def test_fetch_github_issue_success(tool_ctx) -> None:
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
    tool_ctx,
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


@pytest.mark.anyio
async def test_fetch_github_issue_delegates_to_client(tool_ctx):
    mock_client = AsyncMock()
    mock_client.fetch_issue.return_value = {
        "success": True,
        "issue_number": 1,
        "title": "T",
        "url": "u",
        "state": "open",
        "labels": [],
        "content": "# T",
    }
    tool_ctx.github_client = mock_client
    result = json.loads(await fetch_github_issue("owner/repo#1"))
    assert result["success"] is True
    mock_client.fetch_issue.assert_called_once_with("owner/repo#1", include_comments=True)


@pytest.mark.anyio
async def test_fetch_github_issue_client_error_propagated(tool_ctx):
    mock_client = AsyncMock()
    mock_client.fetch_issue.return_value = {"success": False, "error": "Not Found"}
    tool_ctx.github_client = mock_client
    result = json.loads(await fetch_github_issue("owner/repo#404"))
    assert result["success"] is False


def test_fetch_github_issue_in_gated_tools():
    assert "fetch_github_issue" in GATED_TOOLS
    assert "fetch_github_issue" not in UNGATED_TOOLS


def test_fetch_github_issue_docstring_is_role_scoped() -> None:
    """Docstring must not contain a blanket 'use automatically whenever' directive.

    The old directive caused orchestrator sessions to fetch the issue before
    dispatch_food_truck even when only routing the URL downstream as an ingredient,
    wasting ~2K+ tokens per dispatch call.
    """
    doc = fetch_github_issue.__doc__ or ""
    doc_lower = doc.lower()
    assert "automatically whenever" not in doc_lower, (
        "fetch_github_issue docstring must not contain blanket 'automatically whenever' "
        "directive — it causes dispatcher sessions to fetch issues they don't need. "
        "See issue #1170."
    )
    assert "dispatch_food_truck" in doc_lower or "ingredient" in doc_lower, (
        "fetch_github_issue docstring must include role-aware guidance anchored on "
        "'dispatch_food_truck' or 'ingredient' — generic routing words are too loose "
        "to catch accidental docstring regression. See issue #1170."
    )


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
async def test_get_issue_title_success(tool_ctx) -> None:
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


class TestGetIssueTitleTool:
    @pytest.mark.anyio
    async def test_get_issue_title_success(self, tool_ctx):
        """Delegates to github_client.fetch_title; returns JSON result."""
        mock_client = AsyncMock()
        mock_client.fetch_title.return_value = {
            "success": True,
            "number": 42,
            "title": "Fix merge conflict triage",
            "slug": "fix-merge-conflict-triage",
        }
        tool_ctx.github_client = mock_client
        result = json.loads(await get_issue_title("https://github.com/owner/repo/issues/42"))
        assert result["success"] is True
        assert result["number"] == 42
        assert result["title"] == "Fix merge conflict triage"
        assert result["slug"] == "fix-merge-conflict-triage"
        mock_client.fetch_title.assert_called_once_with("https://github.com/owner/repo/issues/42")

    @pytest.mark.anyio
    async def test_get_issue_title_client_error_propagated(self, tool_ctx):
        """Propagates {success: False, error: ...} from fetch_title."""
        mock_client = AsyncMock()
        mock_client.fetch_title.return_value = {"success": False, "error": "Not Found"}
        tool_ctx.github_client = mock_client
        result = json.loads(await get_issue_title("owner/repo#404"))
        assert result["success"] is False

    def test_get_issue_title_is_gated(self):
        """'get_issue_title' in GATED_TOOLS."""
        assert "get_issue_title" in GATED_TOOLS
        assert "get_issue_title" not in UNGATED_TOOLS
