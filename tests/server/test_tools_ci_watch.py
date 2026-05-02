"""Tests for wait_for_ci event validation and null coercion."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools_ci_watch import wait_for_ci
from tests.fakes import InMemoryCIWatcher

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_wait_for_ci_rejects_invalid_event(tool_ctx) -> None:
    tool_ctx.ci_watcher = InMemoryCIWatcher()
    result = json.loads(await wait_for_ci("main", event="made_up_event"))
    assert result["conclusion"] == "error"
    assert "event" in result.get("error", "").lower()


@pytest.mark.anyio
async def test_wait_for_ci_coerces_string_none_to_null(tool_ctx) -> None:
    tool_ctx.ci_watcher = InMemoryCIWatcher(
        wait_result={"run_id": 42, "conclusion": "success", "failed_jobs": []}
    )
    result = json.loads(await wait_for_ci("main", event="None"))
    assert result["conclusion"] != "error" or "event" not in result.get("error", "").lower()
