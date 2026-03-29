"""Tests for server/tools_pr_ops.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools_pr_ops import (
    _close_issues_sequentially,
    _map_api_reviews,
    _map_pr_view_reviews,
    bulk_close_issues,
    get_pr_reviews,
)

# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def test_map_api_reviews_extracts_user_login() -> None:
    """raw=[{"user": {"login": "alice"}, "state": "APPROVED", "body": ""}] → author="alice"."""
    raw = [{"user": {"login": "alice"}, "state": "APPROVED", "body": "LGTM"}]
    result = _map_api_reviews(raw)
    assert result == [{"author": "alice", "state": "APPROVED", "body": "LGTM"}]


def test_map_api_reviews_missing_user() -> None:
    """user=None → author=''."""
    raw = [{"user": None, "state": "COMMENTED", "body": ""}]
    result = _map_api_reviews(raw)
    assert result[0]["author"] == ""


def test_map_pr_view_reviews_extracts_author_login() -> None:
    """gh pr view format (author.login) → mapped correctly."""
    data = {
        "reviews": [{"author": {"login": "bob"}, "state": "CHANGES_REQUESTED", "body": "Fix this"}]
    }
    result = _map_pr_view_reviews(data)
    assert result == [{"author": "bob", "state": "CHANGES_REQUESTED", "body": "Fix this"}]


def test_map_pr_view_reviews_empty() -> None:
    """Empty data or missing 'reviews' key → []."""
    assert _map_pr_view_reviews({}) == []
    assert _map_pr_view_reviews({"reviews": []}) == []


@pytest.mark.anyio
async def test_close_issues_sequentially_all_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    """All gh calls return rc=0 → closed=[1,2], failed=[]."""
    with patch(
        "autoskillit.server.tools_pr_ops._run_subprocess",
        new=AsyncMock(return_value=(0, "", "")),
    ):
        closed, failed = await _close_issues_sequentially([1, 2], "closing", "/tmp")

    assert closed == [1, 2]
    assert failed == []


@pytest.mark.anyio
async def test_close_issues_sequentially_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First rc=0, second rc=1 → closed=[1], failed=[2]."""
    call_count = {"n": 0}

    async def _mock_subprocess(cmd, *, cwd, timeout):
        n = call_count["n"]
        call_count["n"] += 1
        return (0, "", "") if n == 0 else (1, "", "not found")

    with patch(
        "autoskillit.server.tools_pr_ops._run_subprocess",
        new=_mock_subprocess,
    ):
        closed, failed = await _close_issues_sequentially([1, 2], "", "/tmp")

    assert closed == [1]
    assert failed == [2]


# ---------------------------------------------------------------------------
# MCP tool handlers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_pr_reviews_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await get_pr_reviews(42, "/tmp"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_get_pr_reviews_with_repo_success(tool_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """repo provided → gh api repos/{repo}/pulls/123/reviews path used."""
    api_response = json.dumps([{"user": {"login": "alice"}, "state": "APPROVED", "body": ""}])
    with patch(
        "autoskillit.server.tools_pr_ops._run_subprocess",
        new=AsyncMock(return_value=(0, api_response, "")),
    ):
        result = json.loads(await get_pr_reviews(123, "/tmp", repo="owner/repo"))

    assert "reviews" in result
    assert result["reviews"][0]["author"] == "alice"


@pytest.mark.anyio
async def test_get_pr_reviews_without_repo_success(
    tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repo='' → gh pr view 123 --json reviews path used."""
    pr_view_response = json.dumps(
        {"reviews": [{"author": {"login": "bob"}, "state": "CHANGES_REQUESTED", "body": ""}]}
    )
    with patch(
        "autoskillit.server.tools_pr_ops._run_subprocess",
        new=AsyncMock(return_value=(0, pr_view_response, "")),
    ):
        result = json.loads(await get_pr_reviews(123, "/tmp", repo=""))

    assert result["reviews"][0]["author"] == "bob"


@pytest.mark.anyio
async def test_get_pr_reviews_gh_failure(tool_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """gh returns rc=1 → {"success": False, "error": ...}."""
    with patch(
        "autoskillit.server.tools_pr_ops._run_subprocess",
        new=AsyncMock(return_value=(1, "", "repository not found")),
    ):
        result = json.loads(await get_pr_reviews(123, "/tmp", repo="owner/repo"))

    assert result["success"] is False
    assert "repository not found" in result["error"]


@pytest.mark.anyio
async def test_bulk_close_issues_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await bulk_close_issues([1, 2], "", "/tmp"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_bulk_close_issues_all_closed(tool_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """All succeed → {"closed": [1, 2, 3], "failed": []}."""
    with patch(
        "autoskillit.server.tools_pr_ops._run_subprocess",
        new=AsyncMock(return_value=(0, "", "")),
    ):
        result = json.loads(await bulk_close_issues([1, 2, 3], "", "/tmp"))

    assert result["closed"] == [1, 2, 3]
    assert result["failed"] == []


@pytest.mark.anyio
async def test_bulk_close_issues_partial_failure(
    tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mixed outcomes → correct closed/failed split."""
    call_count = {"n": 0}

    async def _mock_subprocess(cmd, *, cwd, timeout):
        n = call_count["n"]
        call_count["n"] += 1
        return (0, "", "") if n % 2 == 0 else (1, "", "error")

    with patch(
        "autoskillit.server.tools_pr_ops._run_subprocess",
        new=_mock_subprocess,
    ):
        result = json.loads(await bulk_close_issues([1, 2, 3], "", "/tmp"))

    assert 1 in result["closed"]
    assert 2 in result["failed"]
    assert 3 in result["closed"]
