"""Tests for fail-label release and claim cleanup."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_issue_labels import claim_issue, release_issue
from tests.server._pipeline_test_helpers import _seed_acknowledged_receipt
from tests.server._tools_issue_labels_test_helpers import (
    calls_for,
    make_release_issue_fake,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_release_issue_fail_label_swap(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake(["in-progress"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = json.loads(
        await release_issue(issue_url="https://github.com/owner/repo/issues/42", fail_label="fail")
    )
    assert result["success"] and result["failed"] and result["fail_label"] == "fail"
    assert calls_for(fake, "ensure_label") == [
        (
            "ensure_label",
            ("owner", "repo", "fail"),
            {"color": "d73a4a", "description": "Recipe execution failed"},
        )
    ]
    assert set(calls_for(fake, "swap_labels")[0][1][3]) == {"in-progress", "queued"}
    assert calls_for(fake, "swap_labels")[0][1][4] == ["fail"]


@pytest.mark.anyio
async def test_release_issue_success_removes_fail_label(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake(["in-progress", "fail", "queued"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = json.loads(
        await release_issue(
            issue_url="https://github.com/owner/repo/issues/42", target_branch="develop"
        )
    )
    assert result["success"] and result["staged"]
    swap = calls_for(fake, "swap_labels")[0][1]
    assert {"in-progress", "fail", "queued"} <= set(swap[3])
    assert "staged" in swap[4]


@pytest.mark.anyio
async def test_release_issue_simple_remove_cleans_fail_label(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake(["in-progress", "fail", "queued"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    assert json.loads(await release_issue(issue_url="https://github.com/owner/repo/issues/42"))[
        "success"
    ]
    assert {"in-progress", "fail", "queued"} <= set(calls_for(fake, "swap_labels")[0][1][3])


@pytest.mark.anyio
async def test_release_issue_refuses_after_infrastructure_fault(
    tool_ctx_kitchen_open, monkeypatch
):
    fake = make_release_issue_fake(["in-progress"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    _seed_acknowledged_receipt(tool_ctx_kitchen_open, fault_domain="infrastructure")
    refused = json.loads(await release_issue(issue_url="https://github.com/owner/repo/issues/42"))
    assert not refused["success"] and refused["subtype"] == "gate_error"
    assert "infrastructure" in refused["result"] and not calls_for(fake, "swap_labels")
    proceeded = json.loads(
        await release_issue(
            issue_url="https://github.com/owner/repo/issues/42",
            infrastructure_fault_override_reason="operator confirmed",
        )
    )
    assert proceeded["success"] and len(calls_for(fake, "swap_labels")) == 1


@pytest.mark.anyio
async def test_claim_issue_removes_fail_label_on_claim(tool_ctx_kitchen_open, monkeypatch):
    fake = make_release_issue_fake(["bug"], repository_labels=["in-progress"])
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", fake)
    result = json.loads(await claim_issue(issue_url="https://github.com/owner/repo/issues/42"))
    assert result["success"] and result["claimed"]
    swap = calls_for(fake, "swap_labels")[0][1]
    assert set(swap[3]) == {"queued", "fail"} and swap[4] == ["in-progress"]
    assert not calls_for(fake, "add_labels")
