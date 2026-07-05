"""Tests for fleet-path error count surfacing — the +N more indicator in dispatch validation.

R4: when fleet dispatch validation produces more errors than the display cap,
the DispatchRejected message must include a '+N more errors' indicator.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fleet._helpers import _no_sleep_quota_checker, _noop_quota_refresher

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_suggestions(n: int) -> list[dict[str, str]]:
    return [
        {"rule": f"rule-{i}", "severity": "error", "message": f"error number {i}"}
        for i in range(n)
    ]


@pytest.mark.anyio
async def test_fleet_dispatch_surfaces_plus_n_more_for_combined_overflow(tool_ctx):
    """5 structural + 5 semantic errors → shown=6, overflow='+4 more errors'."""
    tool_ctx.recipes = MagicMock()
    tool_ctx.recipes.find.return_value = MagicMock()
    tool_ctx.recipes.load.return_value = MagicMock()
    tool_ctx.recipes.load_and_validate.return_value = {
        "valid": False,
        "errors": [f"structural error {i}" for i in range(5)],
        "suggestions": _make_suggestions(5),
    }

    with patch("autoskillit.fleet._api.execute_dispatch", new_callable=AsyncMock):
        from autoskillit.fleet._api import _run_dispatch
        from autoskillit.fleet.state_types import DispatchRejected

        result = await _run_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="test task",
            ingredients={},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **kw: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

    assert isinstance(result.outcome, DispatchRejected)
    assert "+4 more errors" in result.outcome.message, (
        f"Expected '+4 more errors' for 5+5 combined, got: {result.outcome.message!r}"
    )


@pytest.mark.anyio
async def test_fleet_dispatch_no_indicator_at_exactly_six(tool_ctx):
    """3 structural + 3 semantic errors → shown=6, no overflow indicator."""
    tool_ctx.recipes = MagicMock()
    tool_ctx.recipes.find.return_value = MagicMock()
    tool_ctx.recipes.load.return_value = MagicMock()
    tool_ctx.recipes.load_and_validate.return_value = {
        "valid": False,
        "errors": [f"structural error {i}" for i in range(3)],
        "suggestions": _make_suggestions(3),
    }

    with patch("autoskillit.fleet._api.execute_dispatch", new_callable=AsyncMock):
        from autoskillit.fleet._api import _run_dispatch
        from autoskillit.fleet.state_types import DispatchRejected

        result = await _run_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="test task",
            ingredients={},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **kw: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

    assert isinstance(result.outcome, DispatchRejected)
    assert "more errors" not in result.outcome.message, (
        f"Did not expect overflow indicator for exactly 6 shown, got: {result.outcome.message!r}"
    )
