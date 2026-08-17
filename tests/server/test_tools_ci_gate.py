"""Gate-membership and gate-closure tests for the four CI tool surfaces:

- wait_for_ci, get_ci_status, wait_for_merge_queue: gated (the source asserts
  these via GATED_TOOLS / UNGATED_TOOLS membership and behavior under
  DefaultGateState(enabled=False)).
"""

from __future__ import annotations

import json

import pytest

from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.tools.tools_ci_merge_queue import wait_for_merge_queue
from autoskillit.server.tools.tools_ci_watch import get_ci_status, wait_for_ci

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Gate membership
# ---------------------------------------------------------------------------


def test_wait_for_ci_is_gated():
    assert "wait_for_ci" in GATED_TOOLS


def test_get_ci_status_is_gated():
    assert "get_ci_status" in GATED_TOOLS
    assert "get_ci_status" not in UNGATED_TOOLS


# ---------------------------------------------------------------------------
# wait_for_ci gate check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_ci_gate_check(tool_ctx):
    """Gate-closed returns gate_error response."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await wait_for_ci("main"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


# ---------------------------------------------------------------------------
# wait_for_merge_queue
# ---------------------------------------------------------------------------


def test_wait_for_merge_queue_is_gated():
    assert "wait_for_merge_queue" in GATED_TOOLS


@pytest.mark.anyio
async def test_gate_closed_returns_gate_error(tool_ctx):
    """Gate-closed returns gate_error response (watcher not called)."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await wait_for_merge_queue(pr_number=1, target_branch="main", cwd="."))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


# ---------------------------------------------------------------------------
# get_ci_status (gated)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_ci_status_gate_check(tool_ctx):
    """get_ci_status is now gated — returns gate_error when gate is closed."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await get_ci_status(branch="main", cwd="/repo"))
    assert result.get("subtype") == "gate_error"
