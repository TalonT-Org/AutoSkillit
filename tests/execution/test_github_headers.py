"""Tests for the shared github_headers helper and its adoption by all three classes.

These tests are the immunity suite for issue P6-5: triplicated header construction
and the missing User-Agent in DefaultMergeQueueWatcher.
"""

from __future__ import annotations

import pytest

from autoskillit.execution.github import github_headers
from autoskillit.execution.merge_queue import DefaultMergeQueueWatcher

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# ---------------------------------------------------------------------------
# github_headers — unit tests
# ---------------------------------------------------------------------------


def test_github_headers_contains_required_base_keys():
    """github_headers must include Accept, X-GitHub-Api-Version, and User-Agent."""
    h = github_headers(None)
    assert h["Accept"] == "application/vnd.github+json"
    assert h["X-GitHub-Api-Version"] == "2022-11-28"
    assert h["User-Agent"] == "autoskillit"


def test_github_headers_injects_authorization_when_token_provided():
    """github_headers('mytoken') must include Authorization: Bearer mytoken."""
    h = github_headers("mytoken")
    assert h["Authorization"] == "Bearer mytoken"


def test_github_headers_omits_authorization_when_no_token():
    """github_headers(None) must not include an Authorization key."""
    h = github_headers(None)
    assert "Authorization" not in h


def test_github_headers_returns_new_dict_each_call():
    """github_headers must return a fresh dict — callers may mutate it safely."""
    h1 = github_headers(None)
    h2 = github_headers(None)
    assert h1 is not h2


# ---------------------------------------------------------------------------
# DefaultMergeQueueWatcher — User-Agent bug fix
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_queue_watcher_includes_user_agent_in_client_headers():
    """DefaultMergeQueueWatcher must include User-Agent: autoskillit in its client.

    Regression guard for P6-5 bug: merge_queue.py built headers inline in __init__
    and omitted User-Agent.
    """
    watcher = DefaultMergeQueueWatcher(token=None)
    try:
        client_headers = dict(watcher._client.headers)
        assert client_headers.get("user-agent") == "autoskillit"
    finally:
        await watcher.aclose()


@pytest.mark.anyio
async def test_merge_queue_watcher_includes_user_agent_with_token():
    """User-Agent must be present even when a token is provided."""
    watcher = DefaultMergeQueueWatcher(token="tok")
    try:
        client_headers = dict(watcher._client.headers)
        assert client_headers.get("user-agent") == "autoskillit"
    finally:
        await watcher.aclose()
