"""Integration contracts for the outer replay-safe open-kitchen binder."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from autoskillit.pipeline import KitchenOpenPhase, ToolContext, transition_abort
from autoskillit.server.tools.tools_kitchen import (
    _bind_open_kitchen_transition,
    _transition_start,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _transition_handler(
    implementation: Any,
) -> Any:
    async def handler(
        name: str | None = None,
        overrides: dict[str, str] | None = None,
        ingredients_only: bool = False,
        delivery_request: dict[str, object] | None = None,
    ) -> str:
        return await implementation(
            name=name,
            overrides=overrides,
            ingredients_only=ingredients_only,
            delivery_request=delivery_request,
        )

    return _bind_open_kitchen_transition(handler)


@pytest.mark.anyio
async def test_same_intent_overlap_returns_in_progress_then_exact_replay(
    tool_ctx: ToolContext,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    response = '{"success":true,"initialization_id":"init-1"}'

    async def implementation(**_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        tool_ctx.recipe_name = str(_kwargs["name"])
        entered.set()
        await release.wait()
        return response

    handler = _transition_handler(implementation)
    first = asyncio.create_task(handler(name="implementation"))
    await entered.wait()

    overlap = json.loads(await handler(name="implementation"))
    release.set()
    assert await first == response
    replay = await handler(name="implementation")

    assert overlap["success"] is False
    assert overlap["error"] == "open_kitchen_in_progress"
    assert overlap["retry_disposition"] == "in_progress"
    assert replay == response
    assert calls == 1
    assert tool_ctx.kitchen_open_state.phase is KitchenOpenPhase.COMMITTED
    assert tool_ctx.kitchen_open_state.request_active is False


@pytest.mark.anyio
async def test_changed_intent_conflicts_while_operation_is_active(
    tool_ctx: ToolContext,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def implementation(**_kwargs: object) -> str:
        entered.set()
        await release.wait()
        return '{"success":true}'

    handler = _transition_handler(implementation)
    first = asyncio.create_task(handler(name="implementation"))
    await entered.wait()

    conflict = json.loads(await handler(name="remediation"))
    release.set()
    await first

    assert conflict["success"] is False
    assert conflict["error"] == "open_kitchen_intent_fingerprint_conflict"
    assert conflict["retry_disposition"] == "fingerprint_conflict"
    assert tool_ctx.kitchen_open_state.request_active is False


@pytest.mark.anyio
async def test_proven_predispatch_failure_remains_retry_safe(
    tool_ctx: ToolContext,
) -> None:
    calls = 0

    async def implementation(**_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        assert _transition_start(tool_ctx, "recipe_serving") is True
        transition_abort(tool_ctx, "recipe_serving")
        return '{"success":false,"error":"recipe_not_found"}'

    handler = _transition_handler(implementation)

    first = json.loads(await handler(name="missing-recipe"))
    second = json.loads(await handler(name="missing-recipe"))

    assert first["retry_disposition"] == "retry_safe"
    assert second["retry_disposition"] == "retry_safe"
    assert first["phase"] != KitchenOpenPhase.FAILED_AMBIGUOUS.value
    assert calls == 2
    assert all(effect.name != "recipe_serving" for effect in tool_ctx.kitchen_open_state.effects)
