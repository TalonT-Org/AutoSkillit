"""L1 unit tests for execution/github.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from autoskillit.core import GitHubFetcher
from autoskillit.execution.github import (
    DefaultGitHubFetcher,
    _parse_issue_ref,
    parse_merge_queue_response,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

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


# ---------------------------------------------------------------------------
# DefaultGitHubFetcher — fetch_title
# ---------------------------------------------------------------------------

_TITLE_ISSUE_JSON = {
    "number": 42,
    "title": "Fix merge conflict triage",
    "html_url": "https://github.com/owner/repo/issues/42",
    "state": "open",
    "labels": [],
    "body": "Some body text.",
    "comments": 10,
}


class TestFetchTitle:
    @pytest.mark.anyio
    async def test_fetch_title_success(self, httpx_mock):
        """Returns {success, number, title, slug} for a valid issue URL."""
        httpx_mock.add_response(
            url="https://api.github.com/repos/owner/repo/issues/42",
            json=_TITLE_ISSUE_JSON,
        )
        fetcher = DefaultGitHubFetcher(token="test-token")
        result = await fetcher.fetch_title("https://github.com/owner/repo/issues/42")
        assert result["success"] is True
        assert result["number"] == 42
        assert result["title"] == "Fix merge conflict triage"
        assert result["slug"] == "fix-merge-conflict-triage"

    @pytest.mark.anyio
    async def test_fetch_title_slug_generation_special_chars(self, httpx_mock):
        """Slug strips special chars."""
        httpx_mock.add_response(
            url="https://api.github.com/repos/owner/repo/issues/1",
            json={
                "number": 1,
                "title": "feat: Add API (v2) support!",
                "html_url": "https://github.com/owner/repo/issues/1",
                "state": "open",
                "labels": [],
                "body": "",
                "comments": 0,
            },
        )
        fetcher = DefaultGitHubFetcher(token=None)
        result = await fetcher.fetch_title("owner/repo#1")
        assert result["success"] is True
        assert result["slug"] == "feat-add-api-v2-support"

    @pytest.mark.anyio
    async def test_fetch_title_no_comments_fetched(self, httpx_mock):
        """Even if issue has comments, fetch_title makes only one HTTP call."""
        httpx_mock.add_response(
            url="https://api.github.com/repos/owner/repo/issues/42",
            json=_TITLE_ISSUE_JSON,
        )
        fetcher = DefaultGitHubFetcher(token="tok")
        await fetcher.fetch_title("owner/repo#42")
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.anyio
    async def test_fetch_title_404(self, httpx_mock):
        """Returns {success: False, error: ...} on 404."""
        httpx_mock.add_response(
            url="https://api.github.com/repos/owner/repo/issues/99",
            status_code=404,
        )
        fetcher = DefaultGitHubFetcher(token="tok")
        result = await fetcher.fetch_title("owner/repo#99")
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_fetch_title_401_no_token(self, httpx_mock):
        """Returns {success: False, error: ...} on 401; error mentions authentication."""
        httpx_mock.add_response(
            url="https://api.github.com/repos/owner/repo/issues/1",
            status_code=401,
        )
        fetcher = DefaultGitHubFetcher(token="bad-token")
        result = await fetcher.fetch_title("owner/repo#1")
        assert result["success"] is False
        assert "401" in result["error"] or "auth" in result["error"].lower()

    @pytest.mark.anyio
    async def test_fetch_title_network_error(self, httpx_mock):
        """Never raises; returns {success: False, error: ...} on network errors."""
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))
        fetcher = DefaultGitHubFetcher(token=None)
        result = await fetcher.fetch_title("owner/repo#1")
        assert result["success"] is False
        assert "error" in result

    def test_protocol_conformance(self):
        """DefaultGitHubFetcher satisfies GitHubFetcher protocol (has fetch_title)."""
        assert isinstance(DefaultGitHubFetcher(), GitHubFetcher)


# ---------------------------------------------------------------------------
# T2 — _parse_issue_ref source is in core, not github.py
# ---------------------------------------------------------------------------


def test_parse_issue_ref_not_defined_in_github_module():
    import inspect

    import autoskillit.execution.github as gh

    src_file = inspect.getfile(gh._parse_issue_ref)
    assert "github.py" not in src_file
    assert "core" in src_file


# ---------------------------------------------------------------------------
# add_labels
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_labels_posts_to_github_api(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/42/labels",
        method="POST",
        json=[{"name": "in-progress"}, {"name": "bug"}],
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.add_labels("owner", "repo", 42, ["in-progress"])
    assert result["success"] is True
    assert "in-progress" in result["labels"]
    requests = httpx_mock.get_requests()
    assert requests[0].headers.get("authorization") == "Bearer tok"


@pytest.mark.anyio
async def test_add_labels_returns_error_on_404(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/42/labels",
        method="POST",
        status_code=404,
        json={"message": "Not Found"},
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.add_labels("owner", "repo", 42, ["in-progress"])
    assert result["success"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# remove_label
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_remove_label_deletes_from_github_api(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/42/labels/in-progress",
        method="DELETE",
        status_code=200,
        json=[{"name": "bug"}],
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.remove_label("owner", "repo", 42, "in-progress")
    assert result["success"] is True


@pytest.mark.anyio
async def test_remove_label_returns_success_on_404(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/42/labels/in-progress",
        method="DELETE",
        status_code=404,
        json={"message": "Not Found"},
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.remove_label("owner", "repo", 42, "in-progress")
    assert result["success"] is True


# ---------------------------------------------------------------------------
# ensure_label
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_label_creates_when_missing(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/labels",
        method="POST",
        status_code=201,
        json={"name": "in-progress", "color": "fbca04"},
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.ensure_label("owner", "repo", "in-progress")
    assert result["success"] is True
    assert result["created"] is True


@pytest.mark.anyio
async def test_ensure_label_no_op_when_exists(httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/labels",
        method="POST",
        status_code=422,
        json={"message": "Validation Failed"},
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.ensure_label("owner", "repo", "in-progress")
    assert result["success"] is True
    assert result["created"] is False


@pytest.mark.anyio
async def test_ensure_label_returns_error_on_network_failure(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    fetcher = DefaultGitHubFetcher(token="tok")
    result = await fetcher.ensure_label("owner", "repo", "in-progress")
    assert result["success"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# Protocol conformance — new methods
# ---------------------------------------------------------------------------


def test_default_github_fetcher_implements_full_protocol():
    fetcher = DefaultGitHubFetcher(token=None)
    assert isinstance(fetcher, GitHubFetcher)
    for method in (
        "fetch_issue",
        "search_issues",
        "create_issue",
        "add_comment",
        "add_labels",
        "remove_label",
        "ensure_label",
    ):
        assert callable(getattr(fetcher, method, None)), f"missing method: {method}"


# ---------------------------------------------------------------------------
# parse_merge_queue_response
# ---------------------------------------------------------------------------

_QUEUE_RESPONSE_WITH_ENTRIES = {
    "data": {
        "repository": {
            "mergeQueue": {
                "entries": {
                    "nodes": [
                        {
                            "position": 2,
                            "state": "MERGEABLE",
                            "pullRequest": {"number": 42, "title": "feat: add thing"},
                        },
                        {
                            "position": 1,
                            "state": "MERGEABLE",
                            "pullRequest": {"number": 37, "title": "fix: old bug"},
                        },
                        {
                            "position": 3,
                            "state": "AWAITING_CHECKS",
                            "pullRequest": {"number": 55, "title": "wip: not ready"},
                        },
                    ]
                }
            }
        }
    }
}

_QUEUE_RESPONSE_EMPTY = {"data": {"repository": {"mergeQueue": {"entries": {"nodes": []}}}}}

_QUEUE_RESPONSE_NULL_QUEUE = {"data": {"repository": {"mergeQueue": None}}}

_QUEUE_RESPONSE_GRAPHQL_ERROR = {
    "errors": [{"message": "Field 'mergeQueue' doesn't exist on type 'Repository'"}]
}


def test_parse_merge_queue_response_returns_entries_sorted_by_position():
    entries = parse_merge_queue_response(_QUEUE_RESPONSE_WITH_ENTRIES)
    assert len(entries) == 3
    assert entries[0]["position"] == 1
    assert entries[1]["position"] == 2
    assert entries[2]["position"] == 3


def test_parse_merge_queue_response_entry_fields():
    entries = parse_merge_queue_response(_QUEUE_RESPONSE_WITH_ENTRIES)
    first = entries[0]
    assert first["pr_number"] == 37
    assert first["pr_title"] == "fix: old bug"
    assert first["state"] == "MERGEABLE"
    assert first["position"] == 1


def test_parse_merge_queue_response_preserves_all_states():
    entries = parse_merge_queue_response(_QUEUE_RESPONSE_WITH_ENTRIES)
    states = {e["state"] for e in entries}
    assert "MERGEABLE" in states
    assert "AWAITING_CHECKS" in states


def test_parse_merge_queue_response_empty_nodes_returns_empty_list():
    assert parse_merge_queue_response(_QUEUE_RESPONSE_EMPTY) == []


def test_parse_merge_queue_response_null_queue_returns_empty_list():
    assert parse_merge_queue_response(_QUEUE_RESPONSE_NULL_QUEUE) == []


def test_parse_merge_queue_response_graphql_error_returns_empty_list():
    assert parse_merge_queue_response(_QUEUE_RESPONSE_GRAPHQL_ERROR) == []


def test_parse_merge_queue_response_missing_data_key_returns_empty_list():
    assert parse_merge_queue_response({}) == []


def test_parse_merge_queue_response_missing_pullrequest_included_with_none():
    bad = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "entries": {
                        "nodes": [
                            {"position": 1, "state": "MERGEABLE"},  # missing pullRequest key
                        ]
                    }
                }
            }
        }
    }
    entries = parse_merge_queue_response(bad)
    # Missing pullRequest key must not crash; entry is included with pr_number=None
    assert len(entries) == 1
    assert entries[0]["pr_number"] is None


def test_parse_merge_queue_response_is_pure_function():
    """Calling it twice with the same input produces the same output."""
    a = parse_merge_queue_response(_QUEUE_RESPONSE_WITH_ENTRIES)
    b = parse_merge_queue_response(_QUEUE_RESPONSE_WITH_ENTRIES)
    assert a == b


def test_parse_merge_queue_response_bad_node_skips_not_drops_rest():
    """A None node (or other non-dict) skips that entry but preserves valid entries."""
    data = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "entries": {
                        "nodes": [
                            {
                                "position": 1,
                                "state": "MERGEABLE",
                                "pullRequest": {"number": 10, "title": "ok"},
                            },
                            None,  # malformed — should be skipped
                            {
                                "position": 3,
                                "state": "MERGEABLE",
                                "pullRequest": {"number": 30, "title": "also ok"},
                            },
                        ]
                    }
                }
            }
        }
    }
    entries = parse_merge_queue_response(data)
    assert len(entries) == 2
    assert entries[0]["pr_number"] == 10
    assert entries[1]["pr_number"] == 30


def test_parse_merge_queue_response_missing_position_sorts_last():
    """Entries without a position key sort after entries with explicit positions."""
    data = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "entries": {
                        "nodes": [
                            {
                                "state": "MERGEABLE",
                                "pullRequest": {"number": 99, "title": "no pos"},
                            },
                            {
                                "position": 1,
                                "state": "MERGEABLE",
                                "pullRequest": {"number": 1, "title": "pos 1"},
                            },
                        ]
                    }
                }
            }
        }
    }
    entries = parse_merge_queue_response(data)
    assert len(entries) == 2
    assert entries[0]["pr_number"] == 1
    assert entries[1]["pr_number"] == 99


# ---------------------------------------------------------------------------
# T1 — Mutating throttle enforces 1s gap
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mutating_throttle_enforces_delay(httpx_mock):
    """Two consecutive mutating calls sleep to enforce 1s gap."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues",
        method="POST",
        json={"number": 1, "html_url": "https://github.com/owner/repo/issues/1"},
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues",
        method="POST",
        json={"number": 2, "html_url": "https://github.com/owner/repo/issues/2"},
    )
    fetcher = DefaultGitHubFetcher(token="test")
    with patch("autoskillit.execution.github.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await fetcher.create_issue("owner", "repo", "Title 1", "body")
        await fetcher.create_issue("owner", "repo", "Title 2", "body")

    assert mock_sleep.call_count == 1
    sleep_duration = mock_sleep.call_args[0][0]
    assert sleep_duration == pytest.approx(1.0, abs=0.1)


# ---------------------------------------------------------------------------
# T2 — Read-only methods bypass throttle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_methods_bypass_throttle(httpx_mock):
    """fetch_issue does not trigger throttle delay after a mutating call."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues",
        method="POST",
        json={"number": 1, "html_url": "https://github.com/owner/repo/issues/1"},
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1",
        json=_ISSUE_NO_COMMENTS_JSON,
    )
    fetcher = DefaultGitHubFetcher(token="test")
    with patch("autoskillit.execution.github.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await fetcher.create_issue("owner", "repo", "Title", "body")
        await fetcher.fetch_issue("owner/repo#1", include_comments=False)

    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# T3 — ensure_label cache hit skips API call
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_label_cache_hit(httpx_mock):
    """Second ensure_label for same owner/repo/label returns cached result without API call."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/labels",
        method="POST",
        status_code=201,
        json={"name": "bug", "color": "ededed"},
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    with patch("autoskillit.execution.github.asyncio.sleep", new_callable=AsyncMock):
        result1 = await fetcher.ensure_label("owner", "repo", "bug")
        result2 = await fetcher.ensure_label("owner", "repo", "bug")

    assert result1 == {"success": True, "created": True}
    assert result2 == {"success": True, "created": False}
    assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# T4 — ensure_label cache is scoped to owner/repo/label triple
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_label_cache_different_repos(httpx_mock):
    """ensure_label for different repos hits API separately."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo1/labels",
        method="POST",
        status_code=201,
        json={"name": "bug", "color": "ededed"},
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo2/labels",
        method="POST",
        status_code=201,
        json={"name": "bug", "color": "ededed"},
    )
    fetcher = DefaultGitHubFetcher(token="tok")
    with patch("autoskillit.execution.github.asyncio.sleep", new_callable=AsyncMock):
        await fetcher.ensure_label("owner", "repo1", "bug")
        await fetcher.ensure_label("owner", "repo2", "bug")

    assert len(httpx_mock.get_requests()) == 2


# ---------------------------------------------------------------------------
# T8 — Throttle lock serializes concurrent mutating calls
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_throttle_serializes_concurrent_mutating_calls(httpx_mock):
    """Concurrent add_labels + create_issue calls are serialized by the throttle lock."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/42/labels",
        method="POST",
        json=[{"name": "bug"}],
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues",
        method="POST",
        json={"number": 2, "html_url": "https://github.com/owner/repo/issues/2"},
    )
    fetcher = DefaultGitHubFetcher(token="test")
    with patch("autoskillit.execution.github.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await asyncio.gather(
            fetcher.add_labels("owner", "repo", 42, ["bug"]),
            fetcher.create_issue("owner", "repo", "Title", "body"),
        )

    assert mock_sleep.call_count == 1
