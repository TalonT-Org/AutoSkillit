"""L1 unit tests for execution/github.py."""

from __future__ import annotations

import httpx
import pytest

from autoskillit.core import GitHubFetcher
from autoskillit.execution.github import DefaultGitHubFetcher, _parse_issue_ref

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


@pytest.mark.anyio
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


@pytest.mark.anyio
async def test_default_github_fetcher_no_comments_flag(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        json=_ISSUE_JSON,
    )
    fetcher = DefaultGitHubFetcher(token=None)
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    assert result["success"] is True
    assert "## Comments" not in result["content"]


@pytest.mark.anyio
async def test_default_github_fetcher_404(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/999",
        status_code=404,
    )
    fetcher = DefaultGitHubFetcher(token="some-token")
    result = await fetcher.fetch_issue("owner/repo#999", include_comments=False)
    assert result["success"] is False
    assert "error" in result


@pytest.mark.anyio
async def test_default_github_fetcher_auth_failure(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        status_code=401,
    )
    fetcher = DefaultGitHubFetcher(token="bad-token")
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    assert result["success"] is False


@pytest.mark.anyio
async def test_default_github_fetcher_no_token_omits_auth_header(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        json=_ISSUE_NO_COMMENTS_JSON,
    )
    fetcher = DefaultGitHubFetcher(token=None)
    await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert "authorization" not in {k.lower() for k in requests[0].headers}


# ---------------------------------------------------------------------------
# GitHubFetcher protocol — has_token requirement
# ---------------------------------------------------------------------------


def test_github_fetcher_protocol_requires_has_token():
    class NoTokenFetcher:
        async def fetch_issue(self, issue_ref, *, include_comments=True):
            return {}

    assert not isinstance(NoTokenFetcher(), GitHubFetcher)


def test_default_github_fetcher_has_token_true():
    assert DefaultGitHubFetcher(token="abc").has_token is True


def test_default_github_fetcher_has_token_false():
    assert DefaultGitHubFetcher(token=None).has_token is False


# ---------------------------------------------------------------------------
# 404 error message — token-aware auth guidance
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_default_github_fetcher_404_no_token_hints_auth(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        status_code=404,
    )
    fetcher = DefaultGitHubFetcher(token=None)
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    assert result["success"] is False
    error = result["error"].lower()
    assert "private" in error or "token" in error or "auth" in error


@pytest.mark.anyio
async def test_default_github_fetcher_404_with_token_is_plain_not_found(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        status_code=404,
    )
    fetcher = DefaultGitHubFetcher(token="real-token")
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    assert result["success"] is False
    assert "token" not in result["error"].lower()


# ---------------------------------------------------------------------------
# 403 and network error coverage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_default_github_fetcher_403_returns_structured_error(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        status_code=403,
        json={"message": "API rate limit exceeded"},
    )
    fetcher = DefaultGitHubFetcher(token=None)
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    assert result["success"] is False
    assert "403" in result["error"]


@pytest.mark.anyio
async def test_default_github_fetcher_request_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    fetcher = DefaultGitHubFetcher(token=None)
    result = await fetcher.fetch_issue("owner/repo#1", include_comments=False)
    assert result["success"] is False
    assert "request error" in result["error"].lower()


# ---------------------------------------------------------------------------
# DefaultGitHubFetcher — search_issues
# ---------------------------------------------------------------------------

_SEARCH_JSON = {
    "total_count": 1,
    "items": [
        {
            "number": 7,
            "title": "KeyError in recipe/validator.py",
            "html_url": "https://github.com/owner/repo/issues/7",
            "body": "Existing issue body.",
            "state": "open",
        }
    ],
}


@pytest.mark.anyio
async def test_search_issues_success(httpx_mock):
    httpx_mock.add_response(json=_SEARCH_JSON)
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.search_issues("KeyError in recipe/validator", "owner", "repo")
    assert result["success"] is True
    assert result["total_count"] == 1
    assert result["items"][0]["number"] == 7


@pytest.mark.anyio
async def test_search_issues_empty_result(httpx_mock):
    httpx_mock.add_response(json={"total_count": 0, "items": []})
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.search_issues("no match fingerprint", "owner", "repo")
    assert result["success"] is True
    assert result["total_count"] == 0
    assert result["items"] == []


@pytest.mark.anyio
async def test_search_issues_http_error(httpx_mock):
    httpx_mock.add_response(status_code=422, json={"message": "Validation Failed"})
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.search_issues("bad query", "owner", "repo")
    assert result["success"] is False
    assert "422" in result["error"]


@pytest.mark.anyio
async def test_search_issues_request_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.search_issues("fp", "owner", "repo")
    assert result["success"] is False
    assert "request error" in result["error"].lower()


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------

_CREATE_ISSUE_JSON = {
    "number": 42,
    "html_url": "https://github.com/owner/repo/issues/42",
}


@pytest.mark.anyio
async def test_create_issue_success(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues",
        method="POST",
        json=_CREATE_ISSUE_JSON,
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.create_issue(
        "owner", "repo", "Bug title", "## Report\n...", labels=["bug"]
    )
    assert result["success"] is True
    assert result["issue_number"] == 42
    assert result["url"] == "https://github.com/owner/repo/issues/42"


@pytest.mark.anyio
async def test_create_issue_http_error(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues",
        method="POST",
        status_code=403,
        json={"message": "Forbidden"},
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.create_issue("owner", "repo", "Title", "body")
    assert result["success"] is False
    assert "403" in result["error"]


@pytest.mark.anyio
async def test_create_issue_request_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.create_issue("owner", "repo", "Title", "body")
    assert result["success"] is False


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------

_COMMENT_JSON = {
    "id": 99,
    "html_url": "https://github.com/owner/repo/issues/7#issuecomment-99",
}


@pytest.mark.anyio
async def test_add_comment_success(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/7/comments",
        method="POST",
        json=_COMMENT_JSON,
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.add_comment("owner", "repo", 7, "New occurrence details")
    assert result["success"] is True
    assert result["comment_id"] == 99


@pytest.mark.anyio
async def test_add_comment_http_error(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/7/comments",
        method="POST",
        status_code=404,
        json={"message": "Not Found"},
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.add_comment("owner", "repo", 7, "body")
    assert result["success"] is False
    assert "404" in result["error"]


@pytest.mark.anyio
async def test_add_comment_request_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.add_comment("owner", "repo", 7, "body")
    assert result["success"] is False


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_github_fetcher_protocol_includes_write_methods():
    fetcher = DefaultGitHubFetcher(token=None)
    assert isinstance(fetcher, GitHubFetcher)
    assert hasattr(fetcher, "search_issues")
    assert hasattr(fetcher, "create_issue")
    assert hasattr(fetcher, "add_comment")
