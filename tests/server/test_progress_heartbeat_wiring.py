"""Behavioral regression tests: run_skill and dispatch_food_truck emit MCP
progress notifications during their blocking span.

Proves the two real, currently-existing call sites are wired to
`progress_heartbeat()` — fails immediately if either wrapping is later
removed. Deliberately does not generalize to a hypothetical future third
tool; that would need its own test when (if) it exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from autoskillit.fleet import (
    DispatchCompleted,
    DispatchEffectProvenance,
    DispatchResult,
    DispatchStatus,
)
from autoskillit.server._progress_heartbeat import progress_heartbeat
from tests.fakes import InMemoryHeadlessExecutor

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _fast_progress_heartbeat(ctx, **kwargs):
    """Same primitive, accelerated interval — keeps the test sub-second."""
    kwargs["interval"] = 0.005
    return progress_heartbeat(ctx, **kwargs)


class _SlowExecutor(InMemoryHeadlessExecutor):
    """Delays just long enough for the accelerated heartbeat to tick."""

    async def run(self, *args, **kwargs):
        await anyio.sleep(0.03)
        return await super().run(*args, **kwargs)


@pytest.mark.anyio
async def test_run_skill_reports_progress_during_blocking_span(tool_ctx_kitchen_open, monkeypatch):
    from autoskillit.server.tools.tools_execution import run_skill

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.progress_heartbeat",
        _fast_progress_heartbeat,
    )
    tool_ctx_kitchen_open.executor = _SlowExecutor()
    mock_ctx = MagicMock(report_progress=AsyncMock(), info=AsyncMock(), error=AsyncMock())

    await run_skill("/autoskillit:investigate task", "/tmp", ctx=mock_ctx)

    # SlowExecutor sleeps 0.03s; with interval=0.005s we expect ~5 ticks.
    # Require at least 2 so a one-shot call would not satisfy the assertion.
    assert mock_ctx.report_progress.await_count >= 2


@pytest.mark.anyio
async def test_dispatch_food_truck_reports_progress_during_blocking_span(
    tool_ctx_kitchen_open, monkeypatch
):
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_fleet_dispatch.progress_heartbeat",
        _fast_progress_heartbeat,
    )

    async def _slow_execute_dispatch(**kwargs):
        await anyio.sleep(0.03)
        return DispatchResult(
            DispatchCompleted(
                success=True,
                dispatch_status=DispatchStatus.SUCCESS,
                dispatch_id="d1",
                dispatched_session_id="s1",
                reason="",
                token_usage={},
                effect_provenance=DispatchEffectProvenance(operation_id="d1"),
            ),
            per_dispatch_state_path=None,
        )

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_fleet_dispatch.execute_dispatch",
        _slow_execute_dispatch,
    )

    from autoskillit.server.tools.tools_fleet_dispatch import dispatch_food_truck

    mock_ctx = MagicMock(report_progress=AsyncMock(), info=AsyncMock(), error=AsyncMock())

    await dispatch_food_truck(recipe="full-audit", task="audit", ctx=mock_ctx)

    # Same rationale: require periodic ticks, not just one.
    assert mock_ctx.report_progress.await_count >= 2
