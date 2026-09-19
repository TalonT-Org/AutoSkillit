"""Tests for release_issue staged lifecycle behaviour."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_issue_labels import release_issue
from tests.server._tools_issue_labels_test_helpers import (
    calls_for,
    make_release_issue_fake,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


async def _release(**kwargs):
    return json.loads(
        await release_issue(issue_url="https://github.com/owner/repo/issues/42", **kwargs)
    )


@pytest.mark.anyio
async def test_release_issue_non_default_branch_applies_staged(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake(["bug", "in-progress"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert result["success"] and result["staged"] and result["staged_label"] == "staged"
    assert not calls_for(fake, "remove_label") and not calls_for(fake, "add_labels")
    assert calls_for(fake, "ensure_label") == [
        (
            "ensure_label",
            ("owner", "repo", "staged"),
            {"color": "0075ca", "description": "Issue resolved, PR staged for promotion"},
        )
    ]
    assert len(calls_for(fake, "swap_labels")) == 1


@pytest.mark.anyio
async def test_release_issue_default_branch_no_staged(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="main")
    assert result["success"] and result["staged"] is False
    assert not calls_for(fake, "ensure_label") and len(calls_for(fake, "swap_labels")) == 1
    assert not calls_for(fake, "remove_label") and not calls_for(fake, "add_labels")


@pytest.mark.anyio
async def test_release_issue_no_target_branch_no_staged(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release()
    assert result["success"] and result["staged"] is False
    assert len(calls_for(fake, "swap_labels")) == 1


@pytest.mark.anyio
async def test_release_issue_staged_label_idempotent(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake(repository_labels=["staged"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert result["success"] and result["staged"] and len(calls_for(fake, "ensure_label")) == 1


@pytest.mark.anyio
async def test_release_issue_custom_staged_label(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop", staged_label="awaiting-promotion")
    assert result["success"] and result["staged_label"] == "awaiting-promotion"
    assert calls_for(fake, "ensure_label") == [
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
    fake = make_release_issue_fake()
    fake.failure_results["ensure_label"] = {"success": False, "error": "API error"}
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert not result["success"] and "staged label" in result["error"]
    assert not calls_for(fake, "swap_labels")


@pytest.mark.anyio
async def test_release_issue_add_labels_failure_returns_error(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake()
    fake.failure_results["swap_labels"] = {"success": False, "error": "Labels limit"}
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch="develop")
    assert not result["success"] and "staged label" in result["error"]


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
    fake = make_release_issue_fake()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = await _release(target_branch=target_branch)
    assert result["staged"] is expected_staged
    assert (result["staged_label"] is not None) is expected_staged
