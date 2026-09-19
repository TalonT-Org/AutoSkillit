"""Tests for release_issue staged lifecycle behaviour."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_issue_labels import release_issue
from tests.fakes import FakeGitHubFetcher

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]
_KEY = ("owner", "repo", 42)


def _fake(labels: list[str] | None = None, repo_labels: list[str] | None = None):
    return FakeGitHubFetcher(
        issues={_KEY: {"labels": labels or ["in-progress"], "state": "open"}},
        repository_labels={("owner", "repo"): repo_labels or []},
    )


def _calls(fake: FakeGitHubFetcher, name: str):
    return [call for call in fake.call_log if call[0] == name]


async def _release(**kwargs):
    return json.loads(
        await release_issue(issue_url="https://github.com/owner/repo/issues/42", **kwargs)
    )


@pytest.mark.anyio
async def test_release_issue_non_default_branch_applies_staged(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake(["bug", "in-progress"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert result["success"] and result["staged"] and result["staged_label"] == "staged"
    assert not _calls(fake, "remove_label") and not _calls(fake, "add_labels")
    assert _calls(fake, "ensure_label") == [
        (
            "ensure_label",
            ("owner", "repo", "staged"),
            {"color": "0075ca", "description": "Issue resolved, PR staged for promotion"},
        )
    ]
    assert len(_calls(fake, "swap_labels")) == 1


@pytest.mark.anyio
async def test_release_issue_default_branch_no_staged(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="main")
    assert result["success"] and result["staged"] is False
    assert not _calls(fake, "ensure_label") and len(_calls(fake, "swap_labels")) == 1
    assert not _calls(fake, "remove_label") and not _calls(fake, "add_labels")


@pytest.mark.anyio
async def test_release_issue_no_target_branch_no_staged(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release()
    assert result["success"] and result["staged"] is False


@pytest.mark.anyio
async def test_release_issue_staged_label_idempotent(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake(repo_labels=["staged"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert result["success"] and result["staged"] and len(_calls(fake, "ensure_label")) == 1


@pytest.mark.anyio
async def test_release_issue_custom_staged_label(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop", staged_label="awaiting-promotion")
    assert result["success"] and result["staged_label"] == "awaiting-promotion"
    assert _calls(fake, "ensure_label") == [
        (
            "ensure_label",
            ("owner", "repo", "awaiting-promotion"),
            {
                "color": "0075ca",
                "description": "Implementation staged and waiting for promotion to main",
            },
        )
    ]


@pytest.mark.anyio
async def test_release_issue_ensure_label_failure_returns_error(
    tool_ctx_kitchen_open, monkeypatch
):
    fake = _fake()
    fake.failure_results["ensure_label"] = {"success": False, "error": "API error"}
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert not result["success"] and "staged label" in result["error"]
    assert not _calls(fake, "swap_labels")


@pytest.mark.anyio
async def test_release_issue_add_labels_failure_returns_error(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake()
    fake.failure_results["swap_labels"] = {"success": False, "error": "Labels limit"}
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert not result["success"] and "staged label" in result["error"]


@pytest.mark.anyio
async def test_release_issue_staged_null_when_not_staged(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    assert (await _release(target_branch="main"))["staged_label"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "default_base_branch,promotion_target,target_branch,expected_staged",
    [
        ("develop", "main", "develop", True),
        ("develop", "develop", "develop", False),
        ("main", "main", "develop", True),
        ("main", "stable", "stable", False),
        ("main", "stable", "develop", True),
    ],
)
async def test_release_issue_staging_uses_promotion_target(
    tool_ctx_kitchen_open,
    monkeypatch,
    default_base_branch,
    promotion_target,
    target_branch,
    expected_staged,
):
    tool_ctx_kitchen_open.config.branching.default_base_branch = default_base_branch
    tool_ctx_kitchen_open.config.branching.promotion_target = promotion_target
    fake = _fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch=target_branch)
    assert result["staged"] is expected_staged
    assert (result["staged_label"] is not None) is expected_staged


@pytest.mark.anyio
async def test_release_issue_staged_uses_swap_labels(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake(["bug", "in-progress"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert result["success"] and result["staged"] and len(_calls(fake, "swap_labels")) == 1
    assert not _calls(fake, "remove_label") and not _calls(fake, "add_labels")


@pytest.mark.anyio
async def test_release_issue_no_stage_uses_swap_labels(tool_ctx_kitchen_open, monkeypatch):
    fake = _fake(["bug", "in-progress"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release()
    assert result["success"] and result["staged"] is False
    assert len(_calls(fake, "swap_labels")) == 1 and not _calls(fake, "remove_label")
