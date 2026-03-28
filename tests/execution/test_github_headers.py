"""Tests for the shared github_headers helper and its adoption by all three classes.

These tests are the immunity suite for issue P6-5: triplicated header construction
and the missing User-Agent in DefaultMergeQueueWatcher.
"""

from __future__ import annotations

from autoskillit.execution.ci import DefaultCIWatcher
from autoskillit.execution.github import DefaultGitHubFetcher, github_headers
from autoskillit.execution.merge_queue import DefaultMergeQueueWatcher

# ---------------------------------------------------------------------------
# github_headers — unit tests
# ---------------------------------------------------------------------------


def testgithub_headers_contains_required_base_keys():
    """github_headers must include Accept, X-GitHub-Api-Version, and User-Agent."""
    h = github_headers(None)
    assert h["Accept"] == "application/vnd.github+json"
    assert h["X-GitHub-Api-Version"] == "2022-11-28"
    assert h["User-Agent"] == "autoskillit"


def testgithub_headers_injects_authorization_when_token_provided():
    """github_headers('mytoken') must include Authorization: Bearer mytoken."""
    h = github_headers("mytoken")
    assert h["Authorization"] == "Bearer mytoken"


def testgithub_headers_omits_authorization_when_no_token():
    """github_headers(None) must not include an Authorization key."""
    h = github_headers(None)
    assert "Authorization" not in h


def testgithub_headers_returns_new_dict_each_call():
    """github_headers must return a fresh dict — callers may mutate it safely."""
    h1 = github_headers(None)
    h2 = github_headers(None)
    assert h1 is not h2


# ---------------------------------------------------------------------------
# DefaultMergeQueueWatcher — User-Agent bug fix
# ---------------------------------------------------------------------------


def test_merge_queue_watcher_includes_user_agent_in_client_headers():
    """DefaultMergeQueueWatcher must include User-Agent: autoskillit in its client.

    This is the regression guard for the P6-5 bug: merge_queue.py built headers
    inline in __init__ and omitted User-Agent.
    """
    watcher = DefaultMergeQueueWatcher(token=None)
    # httpx.AsyncClient merges headers case-insensitively; check both cases
    client_headers = dict(watcher._client.headers)
    assert client_headers.get("user-agent") == "autoskillit"


def test_merge_queue_watcher_includes_user_agent_with_token():
    """User-Agent must be present even when a token is provided."""
    watcher = DefaultMergeQueueWatcher(token="tok")
    client_headers = dict(watcher._client.headers)
    assert client_headers.get("user-agent") == "autoskillit"


# ---------------------------------------------------------------------------
# Consistency: all three classes produce the same base headers
# ---------------------------------------------------------------------------


def test_all_three_classes_produce_same_base_headers():
    """DefaultGitHubFetcher, DefaultCIWatcher, and github_headers(None) must agree.

    When no token is provided, all three header sources must return the same
    base dict (Accept, X-GitHub-Api-Version, User-Agent). This guards against
    future divergence at a single API version bump.
    """
    expected_base = github_headers(None)
    assert DefaultGitHubFetcher()._headers() == expected_base
    assert DefaultCIWatcher()._headers() == expected_base
