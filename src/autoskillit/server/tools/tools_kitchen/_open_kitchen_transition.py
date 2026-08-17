"""Kitchen transition helpers and the open_kitchen request ContextVar.

Shared by ``_open_kitchen.py`` and ``_get_recipe.py`` — both bind the
request context and call ``_attach_transition_fields`` to project
kitchen-state provenance into response payloads.
"""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from typing import Any, cast

from autoskillit.core import resolve_kitchen_id
from autoskillit.pipeline import (
    KITCHEN_EFFECT_RESPONSE_ENFORCEMENT,
    KitchenEffectPhase,
    KitchenIntentConflict,
    KitchenOpenPhase,
    KitchenRetryDisposition,
    ToolContext,
    bind_kitchen_intent,
    canonical_kitchen_intent_fingerprint,
    claim_kitchen_request,
    commit_kitchen_response,
    confirm_kitchen_effect,
    kitchen_state_payload,
    mark_kitchen_effect_ambiguous,
    new_kitchen_open_state,
    release_kitchen_request,
    start_kitchen_effect,
)

_OPEN_KITCHEN_REQUEST_CTX: ContextVar[ToolContext] = ContextVar("open_kitchen_request_context")


def _ensure_kitchen_transition(tool_ctx: ToolContext) -> None:
    """Create infrastructure identity once, before request arguments are bound."""
    with tool_ctx.kitchen_transition_lock:
        state = tool_ctx.kitchen_open_state
        if state.phase is KitchenOpenPhase.CLOSED:
            state = new_kitchen_open_state(
                kitchen_id=resolve_kitchen_id(),
                context_id=state.context_id,
            )
            tool_ctx.kitchen_open_state = state
        tool_ctx.kitchen_id = state.kitchen_id


def _transition_start(tool_ctx: ToolContext, name: str) -> bool:
    """Journal STARTED and report whether the effect still needs dispatch."""
    with tool_ctx.kitchen_transition_lock:
        existing = next(
            (effect for effect in tool_ctx.kitchen_open_state.effects if effect.name == name),
            None,
        )
        if existing is not None:
            if existing.phase in {
                KitchenEffectPhase.STARTED,
                KitchenEffectPhase.CONFIRMED,
                KitchenEffectPhase.DEGRADED,
            }:
                return False
            if existing.phase is KitchenEffectPhase.AMBIGUOUS:
                raise RuntimeError(f"kitchen effect {name!r} requires reconciliation")
        tool_ctx.kitchen_open_state = start_kitchen_effect(
            tool_ctx.kitchen_open_state,
            name,
        )
    return True


def _transition_fields(tool_ctx: ToolContext, *, committed: bool = False) -> dict[str, Any]:
    if committed:
        _transition_start(tool_ctx, KITCHEN_EFFECT_RESPONSE_ENFORCEMENT)
    with tool_ctx.kitchen_transition_lock:
        payload = kitchen_state_payload(tool_ctx.kitchen_open_state)
    if committed:
        payload["phase"] = KitchenOpenPhase.COMMITTED.value
        payload["retry_disposition"] = KitchenRetryDisposition.COMMITTED_REPLAY.value
        for effect in payload["effects"]:
            if effect["phase"] == KitchenEffectPhase.STARTED.value:
                effect["phase"] = KitchenEffectPhase.CONFIRMED.value
                effect["receipt"] = f"response:{effect['effect_id']}"
    return payload


def _attach_transition_fields(
    result: dict[str, Any],
    tool_ctx: ToolContext,
    *,
    committed: bool,
) -> dict[str, Any]:
    result.update(_transition_fields(tool_ctx, committed=committed))
    return result


def _open_kitchen_conflict_response(
    conflict: KitchenIntentConflict,
) -> str:
    payload = kitchen_state_payload(conflict.state)
    payload.update(
        {
            "success": False,
            "kitchen": "failed",
            "error": "open_kitchen_intent_fingerprint_conflict",
            "received_intent_fingerprint": conflict.received_fingerprint,
            "retry_disposition": KitchenRetryDisposition.FINGERPRINT_CONFLICT.value,
        }
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _read_open_kitchen_request_ctx() -> ToolContext:
    return _OPEN_KITCHEN_REQUEST_CTX.get()


def _open_kitchen_cancellation_response(
    tool_ctx: ToolContext,
    exc: BaseException,
) -> str:
    with tool_ctx.kitchen_transition_lock:
        state = tool_ctx.kitchen_open_state
        started = next(
            (
                effect
                for effect in reversed(state.effects)
                if effect.phase is KitchenEffectPhase.STARTED
            ),
            None,
        )
        if started is not None:
            state = mark_kitchen_effect_ambiguous(
                state,
                started.name,
                evidence=f"{type(exc).__name__}: transport teardown",
            )
            tool_ctx.kitchen_open_state = state
        payload = kitchen_state_payload(state)
    payload.update(
        {
            "success": False,
            "kitchen": "failed",
            "error": "cancelled",
            "subtype": "cancelled",
        }
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _bind_open_kitchen_transition(
    fn: Callable[..., Awaitable[str]],
) -> Callable[..., Awaitable[str]]:
    """Bind request intent outside the typed cancellation boundary."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        from autoskillit.server import _get_ctx  # circular-break

        try:
            tool_ctx = _get_ctx()
        except RuntimeError:
            unshielded = cast(
                Callable[..., Awaitable[str]],
                getattr(fn, "__wrapped__", fn),
            )
            return await unshielded(*args, **kwargs)
        _ensure_kitchen_transition(tool_ctx)
        bound = signature.bind_partial(*args, **kwargs)
        name = bound.arguments.get("name")
        overrides = bound.arguments.get("overrides")
        ingredients_only = bool(bound.arguments.get("ingredients_only", False))
        delivery_request = bound.arguments.get("delivery_request")
        fingerprint = canonical_kitchen_intent_fingerprint(
            name=name if isinstance(name, str) else None,
            overrides=overrides if isinstance(overrides, Mapping) else None,
            ingredients_only=ingredients_only,
            delivery_request=(delivery_request if isinstance(delivery_request, Mapping) else None),
            context_id=tool_ctx.kitchen_open_state.context_id,
        )
        mode = "ingredients_only" if ingredients_only else ("recipe" if name else "anonymous")
        with tool_ctx.kitchen_transition_lock:
            active = tool_ctx.kitchen_open_state
            committed_postconditions_hold = not (
                active.phase is KitchenOpenPhase.COMMITTED
                and mode == "recipe"
                and getattr(tool_ctx, "recipe_name", "") != name
            )
            if (
                active.phase is KitchenOpenPhase.COMMITTED
                and active.intent_fingerprint is not None
                and (active.intent_fingerprint != fingerprint or not committed_postconditions_hold)
            ):
                tool_ctx.kitchen_open_state = new_kitchen_open_state(
                    kitchen_id=active.kitchen_id,
                    context_id=active.context_id,
                )
        try:
            with tool_ctx.kitchen_transition_lock:
                tool_ctx.kitchen_open_state = bind_kitchen_intent(
                    tool_ctx.kitchen_open_state,
                    fingerprint=fingerprint,
                )
                state = tool_ctx.kitchen_open_state
        except KitchenIntentConflict as conflict:
            return _open_kitchen_conflict_response(conflict)
        if state.phase is KitchenOpenPhase.COMMITTED and state.cached_response is not None:
            return state.cached_response
        if state.retry_disposition is KitchenRetryDisposition.RECONCILE_REQUIRED:
            payload = kitchen_state_payload(state)
            payload.update(
                {
                    "success": False,
                    "kitchen": "failed",
                    "error": "open_kitchen_reconciliation_required",
                }
            )
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        with tool_ctx.kitchen_transition_lock:
            state, claimed = claim_kitchen_request(tool_ctx.kitchen_open_state)
            tool_ctx.kitchen_open_state = state
        if not claimed:
            payload = kitchen_state_payload(state)
            payload.update(
                {
                    "success": False,
                    "kitchen": "in_progress",
                    "error": "open_kitchen_in_progress",
                }
            )
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        try:
            token = _OPEN_KITCHEN_REQUEST_CTX.set(tool_ctx)
            try:
                result = await fn(*args, **kwargs)
            finally:
                _OPEN_KITCHEN_REQUEST_CTX.reset(token)

            parsed: dict[str, Any] | None
            try:
                candidate = json.loads(result)
                parsed = candidate if isinstance(candidate, dict) else None
            except (TypeError, json.JSONDecodeError):
                parsed = None
            if parsed is not None and parsed.get("success") is True:
                initialization_id = parsed.get("initialization_id")
                with tool_ctx.kitchen_transition_lock:
                    state = tool_ctx.kitchen_open_state
                    for effect in state.effects:
                        if effect.phase is KitchenEffectPhase.STARTED:
                            state = confirm_kitchen_effect(
                                state,
                                effect.name,
                                receipt=f"response:{effect.effect_id}",
                            )
                    tool_ctx.kitchen_open_state = commit_kitchen_response(
                        state,
                        response=result,
                        initialization_id=(
                            initialization_id if isinstance(initialization_id, str) else None
                        ),
                    )
                return result
            if parsed is not None:
                with tool_ctx.kitchen_transition_lock:
                    state = tool_ctx.kitchen_open_state
                    started = next(
                        (
                            effect
                            for effect in reversed(state.effects)
                            if effect.phase is KitchenEffectPhase.STARTED
                        ),
                        None,
                    )
                    if started is not None:
                        state = mark_kitchen_effect_ambiguous(
                            state,
                            started.name,
                            evidence=f"application failure after {started.name} dispatch",
                        )
                        tool_ctx.kitchen_open_state = state
                parsed.update(_transition_fields(tool_ctx))
                return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            return result
        finally:
            with tool_ctx.kitchen_transition_lock:
                tool_ctx.kitchen_open_state = release_kitchen_request(tool_ctx.kitchen_open_state)

    return wrapper
