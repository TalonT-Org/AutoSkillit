"""Tests for check_repo_merge_state MCP tool handler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from autoskillit.server.tools.tools_ci import check_repo_merge_state

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# check_repo_merge_state: token_factory and http_status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_repo_merge_state_uses_token_factory(tool_ctx, monkeypatch):
    """check_repo_merge_state calls token_factory() when set, not config.github.token."""
    resolved_calls = []

    def factory():
        resolved_calls.append(1)
        return "factory-token"

    tool_ctx.token_factory = factory
    tool_ctx.config.github.token = "config-token"

    captured_tokens = []

    async def fake_fetch(owner, repo, branch, token):
        captured_tokens.append(token)
        return {
            "queue_available": False,
            "merge_group_trigger": False,
            "auto_merge_available": False,
            "ci_event": None,
        }

    monkeypatch.setattr("autoskillit.server.tools.tools_ci.fetch_repo_merge_state", fake_fetch)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_ci.resolve_repo_from_remote",
        AsyncMock(return_value="owner/repo"),
    )

    await check_repo_merge_state(branch="main")
    assert captured_tokens == ["factory-token"]
    assert resolved_calls == [1]


@pytest.mark.anyio
async def test_check_repo_merge_state_falls_back_to_config_token_when_no_factory(
    tool_ctx, monkeypatch
):
    """When token_factory is None, config.github.token is used."""
    tool_ctx.token_factory = None
    tool_ctx.config.github.token = "config-token"

    captured_tokens = []

    async def fake_fetch(owner, repo, branch, token):
        captured_tokens.append(token)
        return {
            "queue_available": False,
            "merge_group_trigger": False,
            "auto_merge_available": False,
            "ci_event": None,
        }

    monkeypatch.setattr("autoskillit.server.tools.tools_ci.fetch_repo_merge_state", fake_fetch)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_ci.resolve_repo_from_remote",
        AsyncMock(return_value="owner/repo"),
    )

    await check_repo_merge_state(branch="main")
    assert captured_tokens == ["config-token"]


@pytest.mark.anyio
async def test_check_repo_merge_state_error_includes_http_status(tool_ctx, monkeypatch):
    """HTTP error response envelope contains http_status field."""

    async def fake_fetch(owner, repo, branch, token):
        response = httpx.Response(
            403,
            request=httpx.Request("POST", "https://api.github.com/graphql"),
        )
        raise httpx.HTTPStatusError("403 Forbidden", request=response.request, response=response)

    monkeypatch.setattr("autoskillit.server.tools.tools_ci.fetch_repo_merge_state", fake_fetch)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_ci.resolve_repo_from_remote",
        AsyncMock(return_value="owner/repo"),
    )

    result = json.loads(await check_repo_merge_state(branch="main"))
    assert "http_status" in result
