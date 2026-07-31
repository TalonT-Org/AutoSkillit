"""Replay-safe, process-local state for the ``open_kitchen`` transition."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

__all__ = [
    "KitchenEffectPhase",
    "KitchenEffectRecord",
    "KitchenIntentConflict",
    "KitchenOpenPhase",
    "KitchenOpenState",
    "KitchenRetryDisposition",
    "KitchenTransitionToken",
    "advance_kitchen_phase",
    "abort_kitchen_effect",
    "bind_kitchen_intent",
    "canonical_kitchen_intent_fingerprint",
    "claim_kitchen_request",
    "closed_kitchen_open_state",
    "commit_kitchen_response",
    "confirm_kitchen_effect",
    "kitchen_state_payload",
    "mark_kitchen_effect_ambiguous",
    "mark_kitchen_effect_degraded",
    "new_kitchen_open_state",
    "prepare_kitchen_response",
    "release_kitchen_request",
    "start_kitchen_effect",
    "transition_abort",
    "transition_ambiguous",
    "transition_confirm",
    "transition_degraded",
]


class KitchenOpenPhase(StrEnum):
    """Authoritative lifecycle phases for one process-local open operation."""

    CLOSED = "closed"
    INFRASTRUCTURE_READY = "infrastructure_ready"
    REQUEST_BOUND = "request_bound"
    VISIBILITY_READY = "visibility_ready"
    RESPONSE_PREPARED = "response_prepared"
    COMMITTED = "committed"
    FAILED_AMBIGUOUS = "failed_ambiguous"


class KitchenEffectPhase(StrEnum):
    """Per-effect journal phases."""

    PREPARED = "prepared"
    STARTED = "started"
    CONFIRMED = "confirmed"
    DEGRADED = "degraded"
    AMBIGUOUS = "ambiguous"


class KitchenRetryDisposition(StrEnum):
    """What a caller may safely do with the current open operation."""

    RETRY_SAFE = "retry_safe"
    IN_PROGRESS = "in_progress"
    RECONCILE_REQUIRED = "reconcile_required"
    FINGERPRINT_CONFLICT = "fingerprint_conflict"
    COMMITTED_REPLAY = "committed_replay"


@dataclass(frozen=True, slots=True)
class KitchenEffectRecord:
    """Immutable receipt for one effect owned by an open operation."""

    name: str
    effect_id: str
    phase: KitchenEffectPhase = KitchenEffectPhase.PREPARED
    receipt: str | None = None
    downstream_identity: str | None = None
    degraded_evidence: str | None = None
    ambiguity: str | None = None


@dataclass(frozen=True, slots=True)
class KitchenTransitionToken:
    """Carrier proving which open operation owns response enforcement."""

    operation_id: str
    effect_id: str


@dataclass(frozen=True, slots=True)
class KitchenOpenState:
    """Immutable snapshot of one process-local ``open_kitchen`` operation."""

    phase: KitchenOpenPhase
    kitchen_id: str
    operation_id: str
    context_id: str
    intent_fingerprint: str | None = None
    effects: tuple[KitchenEffectRecord, ...] = ()
    cached_response: str | None = None
    initialization_id: str | None = None
    degraded_evidence: tuple[str, ...] = ()
    ambiguity: tuple[str, ...] = ()
    retry_disposition: KitchenRetryDisposition = KitchenRetryDisposition.RETRY_SAFE
    request_active: bool = False


class KitchenIntentConflict(ValueError):
    """The active operation was replayed with semantically different intent."""

    def __init__(self, state: KitchenOpenState, received_fingerprint: str) -> None:
        super().__init__("open_kitchen operation intent fingerprint conflict")
        self.state = state
        self.received_fingerprint = received_fingerprint


_LEGAL_PHASE_TRANSITIONS: dict[KitchenOpenPhase, frozenset[KitchenOpenPhase]] = {
    KitchenOpenPhase.CLOSED: frozenset({KitchenOpenPhase.INFRASTRUCTURE_READY}),
    KitchenOpenPhase.INFRASTRUCTURE_READY: frozenset(
        {KitchenOpenPhase.REQUEST_BOUND, KitchenOpenPhase.FAILED_AMBIGUOUS}
    ),
    KitchenOpenPhase.REQUEST_BOUND: frozenset(
        {
            KitchenOpenPhase.VISIBILITY_READY,
            KitchenOpenPhase.RESPONSE_PREPARED,
            KitchenOpenPhase.FAILED_AMBIGUOUS,
        }
    ),
    KitchenOpenPhase.VISIBILITY_READY: frozenset(
        {KitchenOpenPhase.RESPONSE_PREPARED, KitchenOpenPhase.FAILED_AMBIGUOUS}
    ),
    KitchenOpenPhase.RESPONSE_PREPARED: frozenset(
        {KitchenOpenPhase.COMMITTED, KitchenOpenPhase.FAILED_AMBIGUOUS}
    ),
    KitchenOpenPhase.COMMITTED: frozenset(),
    KitchenOpenPhase.FAILED_AMBIGUOUS: frozenset(),
}


def closed_kitchen_open_state(*, context_id: str | None = None) -> KitchenOpenState:
    """Build the sentinel state used before bootstrap and after close."""
    return KitchenOpenState(
        phase=KitchenOpenPhase.CLOSED,
        kitchen_id="",
        operation_id="",
        context_id=(context_id if isinstance(context_id, str) and context_id else str(uuid4())),
    )


def new_kitchen_open_state(
    *,
    kitchen_id: str,
    context_id: str,
    operation_id: str | None = None,
) -> KitchenOpenState:
    """Mint the infrastructure transition before request arguments are bound."""
    if not kitchen_id:
        raise ValueError("kitchen_id must be non-empty")
    return KitchenOpenState(
        phase=KitchenOpenPhase.INFRASTRUCTURE_READY,
        kitchen_id=kitchen_id,
        operation_id=operation_id or str(uuid4()),
        context_id=context_id,
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def canonical_kitchen_intent_fingerprint(
    *,
    name: str | None,
    overrides: Mapping[str, Any] | None,
    ingredients_only: bool,
    delivery_request: Mapping[str, Any] | None,
    context_id: str,
) -> str:
    """Hash normalized response-shaping intent separately from operation identity."""
    payload = {
        "name": name.strip() if isinstance(name, str) else None,
        "overrides": _canonicalize(overrides or {}),
        "ingredients_only": bool(ingredients_only),
        "delivery_request": _canonicalize(delivery_request),
        "context_id": context_id,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _effect_id(operation_id: str, name: str) -> str:
    digest = hashlib.sha256(f"{operation_id}\0{name}".encode()).hexdigest()
    return f"kfx_{digest[:24]}"


def _replace_effect(
    state: KitchenOpenState,
    record: KitchenEffectRecord,
) -> KitchenOpenState:
    effects = tuple(item for item in state.effects if item.name != record.name) + (record,)
    return replace(state, effects=effects)


def _find_effect(state: KitchenOpenState, name: str) -> KitchenEffectRecord | None:
    return next((effect for effect in state.effects if effect.name == name), None)


def advance_kitchen_phase(
    state: KitchenOpenState,
    phase: KitchenOpenPhase,
) -> KitchenOpenState:
    """Advance through a legal phase edge without accepting raw-string states."""
    if phase is state.phase:
        return state
    if phase not in _LEGAL_PHASE_TRANSITIONS[state.phase]:
        raise ValueError(f"illegal kitchen transition: {state.phase.value} -> {phase.value}")
    return replace(state, phase=phase)


def bind_kitchen_intent(
    state: KitchenOpenState,
    *,
    fingerprint: str,
) -> KitchenOpenState:
    """Atomically attach immutable request semantics to the infrastructure state."""
    if state.phase is KitchenOpenPhase.CLOSED:
        raise ValueError("kitchen infrastructure must exist before request binding")
    if state.intent_fingerprint is not None:
        if state.intent_fingerprint != fingerprint:
            raise KitchenIntentConflict(state, fingerprint)
        return state
    bound = replace(
        state,
        intent_fingerprint=fingerprint,
        retry_disposition=KitchenRetryDisposition.IN_PROGRESS,
    )
    bound = advance_kitchen_phase(bound, KitchenOpenPhase.REQUEST_BOUND)
    bound = start_kitchen_effect(bound, "request_binding")
    return confirm_kitchen_effect(
        bound,
        "request_binding",
        receipt=fingerprint,
        downstream_identity=state.operation_id,
    )


def claim_kitchen_request(
    state: KitchenOpenState,
) -> tuple[KitchenOpenState, bool]:
    """Serialize one live request per open operation."""
    if state.request_active:
        return (
            replace(state, retry_disposition=KitchenRetryDisposition.IN_PROGRESS),
            False,
        )
    return (
        replace(
            state,
            request_active=True,
            retry_disposition=KitchenRetryDisposition.IN_PROGRESS,
        ),
        True,
    )


def release_kitchen_request(state: KitchenOpenState) -> KitchenOpenState:
    """Release request ownership without changing the operation outcome."""
    return replace(state, request_active=False)


def start_kitchen_effect(state: KitchenOpenState, name: str) -> KitchenOpenState:
    """Persist STARTED before dispatching an effect."""
    current = _find_effect(state, name)
    if current is not None:
        if current.phase in {
            KitchenEffectPhase.CONFIRMED,
            KitchenEffectPhase.DEGRADED,
            KitchenEffectPhase.AMBIGUOUS,
        }:
            return state
        if current.phase is KitchenEffectPhase.STARTED:
            return state
    record = KitchenEffectRecord(
        name=name,
        effect_id=_effect_id(state.operation_id, name),
        phase=KitchenEffectPhase.STARTED,
    )
    return _replace_effect(
        replace(state, retry_disposition=KitchenRetryDisposition.IN_PROGRESS),
        record,
    )


def abort_kitchen_effect(state: KitchenOpenState, name: str) -> KitchenOpenState:
    """Forget a proven pre-dispatch failure so the effect may be retried safely."""
    current = _find_effect(state, name)
    if current is None:
        return state
    if current.phase is not KitchenEffectPhase.STARTED:
        raise ValueError(
            f"kitchen effect {name!r} cannot be safely aborted from {current.phase.value}"
        )
    return replace(
        state,
        effects=tuple(effect for effect in state.effects if effect.name != name),
        retry_disposition=KitchenRetryDisposition.RETRY_SAFE,
    )


def confirm_kitchen_effect(
    state: KitchenOpenState,
    name: str,
    *,
    receipt: str,
    downstream_identity: str | None = None,
) -> KitchenOpenState:
    """Record authoritative effect confirmation."""
    current = _find_effect(state, name)
    if current is None or current.phase is KitchenEffectPhase.PREPARED:
        raise ValueError(f"kitchen effect {name!r} was not started")
    if current.phase is KitchenEffectPhase.AMBIGUOUS:
        raise ValueError(f"kitchen effect {name!r} requires reconciliation")
    return _replace_effect(
        state,
        replace(
            current,
            phase=KitchenEffectPhase.CONFIRMED,
            receipt=receipt,
            downstream_identity=downstream_identity,
            degraded_evidence=None,
            ambiguity=None,
        ),
    )


def mark_kitchen_effect_degraded(
    state: KitchenOpenState,
    name: str,
    *,
    evidence: str,
) -> KitchenOpenState:
    """Record a best-effort effect whose failure does not invalidate the response."""
    current = _find_effect(state, name)
    if current is None:
        state = start_kitchen_effect(state, name)
        current = _find_effect(state, name)
    assert current is not None
    updated = _replace_effect(
        state,
        replace(
            current,
            phase=KitchenEffectPhase.DEGRADED,
            degraded_evidence=evidence,
        ),
    )
    return replace(
        updated,
        degraded_evidence=updated.degraded_evidence + (evidence,),
    )


def mark_kitchen_effect_ambiguous(
    state: KitchenOpenState,
    name: str,
    *,
    evidence: str,
) -> KitchenOpenState:
    """Record loss of knowledge after effect dispatch."""
    current = _find_effect(state, name)
    if current is None:
        state = start_kitchen_effect(state, name)
        current = _find_effect(state, name)
    assert current is not None
    updated = _replace_effect(
        state,
        replace(
            current,
            phase=KitchenEffectPhase.AMBIGUOUS,
            ambiguity=evidence,
        ),
    )
    return replace(
        updated,
        phase=KitchenOpenPhase.FAILED_AMBIGUOUS,
        ambiguity=updated.ambiguity + (evidence,),
        retry_disposition=KitchenRetryDisposition.RECONCILE_REQUIRED,
    )


def prepare_kitchen_response(
    state: KitchenOpenState,
    *,
    initialization_id: str | None = None,
) -> KitchenOpenState:
    """Advance after the response carrier has been produced."""
    if state.phase is KitchenOpenPhase.REQUEST_BOUND:
        state = advance_kitchen_phase(state, KitchenOpenPhase.RESPONSE_PREPARED)
    elif state.phase is KitchenOpenPhase.VISIBILITY_READY:
        state = advance_kitchen_phase(state, KitchenOpenPhase.RESPONSE_PREPARED)
    elif state.phase not in {
        KitchenOpenPhase.RESPONSE_PREPARED,
        KitchenOpenPhase.COMMITTED,
    }:
        raise ValueError(f"cannot prepare response from {state.phase.value}")
    return replace(state, initialization_id=initialization_id)


def commit_kitchen_response(
    state: KitchenOpenState,
    *,
    response: str,
    initialization_id: str | None = None,
) -> KitchenOpenState:
    """Cache exact enforced terminal bytes and mark the operation committed."""
    prepared = prepare_kitchen_response(state, initialization_id=initialization_id)
    if prepared.phase is not KitchenOpenPhase.COMMITTED:
        prepared = advance_kitchen_phase(prepared, KitchenOpenPhase.COMMITTED)
    return replace(
        prepared,
        cached_response=response,
        retry_disposition=KitchenRetryDisposition.COMMITTED_REPLAY,
    )


def kitchen_state_payload(state: KitchenOpenState) -> dict[str, Any]:
    """Serialize exact operation/effect provenance for MCP result envelopes."""
    return {
        "kitchen_id": state.kitchen_id,
        "operation_id": state.operation_id,
        "intent_fingerprint": state.intent_fingerprint,
        "phase": state.phase.value,
        "effects": [
            {
                "name": effect.name,
                "effect_id": effect.effect_id,
                "phase": effect.phase.value,
                "receipt": effect.receipt,
                "downstream_identity": effect.downstream_identity,
                "degraded_evidence": effect.degraded_evidence,
                "ambiguity": effect.ambiguity,
            }
            for effect in state.effects
        ],
        "degraded_evidence": list(state.degraded_evidence),
        "ambiguity": list(state.ambiguity),
        "retry_disposition": state.retry_disposition.value,
        "initialization_id": state.initialization_id,
    }


def transition_abort(tool_ctx: ToolContext, name: str) -> None:
    """Restore retry-safe state after a proven pre-dispatch application failure."""
    with tool_ctx.kitchen_transition_lock:
        tool_ctx.kitchen_open_state = abort_kitchen_effect(
            tool_ctx.kitchen_open_state,
            name,
        )


def transition_confirm(
    tool_ctx: ToolContext,
    name: str,
    *,
    receipt: str,
    downstream_identity: str | None = None,
) -> None:
    with tool_ctx.kitchen_transition_lock:
        tool_ctx.kitchen_open_state = confirm_kitchen_effect(
            tool_ctx.kitchen_open_state,
            name,
            receipt=receipt,
            downstream_identity=downstream_identity,
        )


def transition_degraded(
    tool_ctx: ToolContext,
    name: str,
    exc: BaseException,
) -> None:
    evidence = f"{type(exc).__name__}: {exc}"
    with tool_ctx.kitchen_transition_lock:
        tool_ctx.kitchen_open_state = mark_kitchen_effect_degraded(
            tool_ctx.kitchen_open_state,
            name,
            evidence=evidence,
        )


def transition_ambiguous(
    tool_ctx: ToolContext,
    name: str,
    exc: BaseException,
) -> None:
    evidence = f"{type(exc).__name__}: {exc}"
    with tool_ctx.kitchen_transition_lock:
        tool_ctx.kitchen_open_state = mark_kitchen_effect_ambiguous(
            tool_ctx.kitchen_open_state,
            name,
            evidence=evidence,
        )
