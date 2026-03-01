"""Tests for fetch_github_issue MCP tool and execution/github.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import GitHubFetcher
from autoskillit.execution.github import DefaultGitHubFetcher, _parse_issue_ref
from autoskillit.pipeline.gate import GATED_TOOLS
from autoskillit.server.tools_integrations import fetch_github_issue
from autoskillit.server.tools_status import kitchen_status

# ---------------------------------------------------------------------------
# _parse_issue_ref unit tests
# ---------------------------------------------------------------------------


def test_parse_issue_ref_full_url():
    owner, repo, number = _parse_issue_ref("https://github.com/acme/myrepo/issues/42")
    assert owner == "acme" and repo == "myrepo" and number == 42


def test_parse_issue_ref_shorthand():
    owner, repo, number = _parse_issue_ref("acme/myrepo#42")
    assert owner == "acme" and repo == "myrepo" and number == 42


def test_parse_issue_ref_invalid_raises():
    with pytest.raises(ValueError):
        _parse_issue_ref("not-a-valid-ref")


def test_parse_issue_ref_bare_number_raises_without_context():
    # bare number alone is not parseable by _parse_issue_ref;
    # resolution is the caller's (tool handler's) responsibility
    with pytest.raises(ValueError):
        _parse_issue_ref("42")


# ---------------------------------------------------------------------------
# DefaultGitHubFetcher unit tests (httpx mocked via pytest-httpx)
# ---------------------------------------------------------------------------

_ISSUE_JSON = {
    "number": 1,
    "title": "Test Issue",
    "html_url": "https://github.com/owner/repo/issues/1",
    "state": "open",
    "labels": [{"name": "bug"}],
    "body": "Issue body here.",
    "comments": 1,
}

_COMMENTS_JSON = [
    {"user": {"login": "commenter"}, "body": "A comment"},
]

_ISSUE_NO_COMMENTS_JSON = {
    "number": 1,
    "title": "Test Issue",
    "html_url": "https://github.com/owner/repo/issues/1",
    "state": "open",
    "labels": [],
    "body": "Issue body here.",
    "comments": 0,
}


@pytest.mark.asyncio
async def test_default_github_fetcher_success(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        json=_ISSUE_JSON,
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1/comments?per_page=100",
        json=_COMMENTS_JSON,
    )
    fetcher = DefaultGitHubFetcher(token="test-token")
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=True)
    assert result["success"] is True
    assert result["issue_number"] == 1
    assert result["title"] == "Test Issue"
    assert "## Comments" in result["content"]
    assert "commenter" in result["content"]


@pytest.mark.asyncio
async def test_default_github_fetcher_no_comments_flag(httpx_mock):
    # Only the issue endpoint should be called (no comments call)
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        json=_ISSUE_JSON,
    )
    fetcher = DefaultGitHubFetcher(token=None)
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    assert result["success"] is True
    assert "## Comments" not in result["content"]


@pytest.mark.asyncio
async def test_default_github_fetcher_404(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/999",
        status_code=404,
    )
    fetcher = DefaultGitHubFetcher(token=None)
    result = await fetcher.fetch_issue("owner/repo#999", include_comments=False)
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_default_github_fetcher_auth_failure(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        status_code=401,
    )
    fetcher = DefaultGitHubFetcher(token="bad-token")
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_default_github_fetcher_no_token_omits_auth_header(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        json=_ISSUE_NO_COMMENTS_JSON,
    )
    fetcher = DefaultGitHubFetcher(token=None)
    await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    requests = httpx_mock.get_requests()
    assert len(requests) >= 1
    assert "authorization" not in {k.lower() for k in requests[0].headers}


# ---------------------------------------------------------------------------
# MCP tool handler tests (using tool_ctx fixture)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_github_issue_gate_closed(tool_ctx):
    tool_ctx.gate.disable()
    result = json.loads(await fetch_github_issue("owner/repo#1"))
    assert result["success"] is False
    assert "gate" in result["result"].lower() or "not enabled" in result["result"].lower()


@pytest.mark.asyncio
async def test_fetch_github_issue_no_client(tool_ctx):
    tool_ctx.github_client = None
    result = json.loads(await fetch_github_issue("owner/repo#1"))
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_fetch_github_issue_bare_number_with_default_repo(tool_ctx):
    tool_ctx.config.github.default_repo = "owner/repo"
    mock_client = AsyncMock()
    mock_client.fetch_issue.return_value = {
        "success": True,
        "issue_number": 42,
        "title": "T",
        "url": "u",
        "state": "open",
        "labels": [],
        "content": "# T",
    }
    tool_ctx.github_client = mock_client
    result = json.loads(await fetch_github_issue("42"))
    assert result["success"] is True
    mock_client.fetch_issue.assert_called_once_with("owner/repo#42", include_comments=True)


@pytest.mark.asyncio
async def test_fetch_github_issue_bare_number_no_default_repo(tool_ctx):
    tool_ctx.config.github.default_repo = None
    tool_ctx.github_client = AsyncMock()
    result = json.loads(await fetch_github_issue("42"))
    assert result["success"] is False
    assert "default_repo" in result["error"]


@pytest.mark.asyncio
async def test_fetch_github_issue_client_error_propagated(tool_ctx):
    mock_client = AsyncMock()
    mock_client.fetch_issue.return_value = {"success": False, "error": "Not Found"}
    tool_ctx.github_client = mock_client
    result = json.loads(await fetch_github_issue("owner/repo#404"))
    assert result["success"] is False


# ---------------------------------------------------------------------------
# Gate and config tests
# ---------------------------------------------------------------------------


def test_fetch_github_issue_in_gated_tools():
    assert "fetch_github_issue" in GATED_TOOLS


def test_github_config_defaults():
    config = AutomationConfig()
    assert config.github.token is None
    assert config.github.default_repo is None


@pytest.mark.asyncio
async def test_kitchen_status_includes_github_config(tool_ctx):
    tool_ctx.config.github.default_repo = "owner/repo"
    status = json.loads(await kitchen_status())
    assert "github_default_repo" in status
    assert status["github_default_repo"] == "owner/repo"
    assert "github_token_configured" in status


def test_github_fetcher_protocol_satisfied():
    fetcher = DefaultGitHubFetcher(token=None)
    assert isinstance(fetcher, GitHubFetcher)
