"""Pure reducer and coverage resolver for cumulative context admission.

The reducer is split across dispatch-category shards co-located in this
package; this module is the thin gateway that preserves the 8-name public
surface, the match/case dispatcher, the reducer registry contract, and the
coverage resolver, while the per-event handlers live in the sibling
``context_admission_*`` modules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import assert_never

from .context_admission_accept_release import _accept, _release_or_rollback
from .context_admission_expiry_rollover import _expire_idempotency, _rollover
from .context_admission_generation import (
    _mark_generation_indeterminate,
    _reconcile_generation,
    _start_generation,
)
from .context_admission_helpers import _effect_coordinates, _publish
from .context_admission_indeterminate import (
    _mark_indeterminate,
    _request_reconciliation,
    _resolve_indeterminate_accepted,
)
from .context_admission_prepare_stage_dispatch import _dispatch, _prepare, _stage
from .context_admission_propose_reserve import _open_epoch, _preflight, _propose, _reserve
from .types._type_context_admission import (
    CONTEXT_ADMISSION_COVERAGE,
    AcceptInputEvent,
    ActiveContextAdmissionState,
    AdmissionReplay,
    AdmissionTransition,
    AuthorityUnavailableEffect,
    AuthorityUnavailableEvent,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionValidationError,
    DispatchRequestEvent,
    ExpireIdempotencyKeyEvent,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    OpenEpochEvent,
    PrepareBatchEvent,
    ProducerCoverageDef,
    ProposeOccurrenceEvent,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    RequestReconciliationEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    RolloverEpochEvent,
    StageHistoryEvent,
    StartGenerationEvent,
    UnsupportedContextAdmissionProtocolError,
)
from .types._type_enums import (
    AdmissionDecisionKind,
    CoverageState,
    ProducerSurface,
)

__all__ = [
    "ContextAdmissionValidationError",
    "UnsupportedContextAdmissionProtocolError",
    "ContextAdmissionReducerDef",
    "CONTEXT_ADMISSION_REDUCER_REGISTRY",
    "context_admission_reducer_for_protocol",
    "reduce_context_admission",
    "replay_context_admission",
    "resolve_context_admission_coverage",
]


@dataclass(frozen=True, slots=True)
class ContextAdmissionReducerDef:
    """Static reducer/replay definition for one released protocol version."""

    protocol_version: int
    reduce_transition: Callable[
        [ContextAdmissionState, ContextAdmissionEvent],
        AdmissionTransition,
    ]
    replay_stream: Callable[
        [ContextAdmissionState, tuple[ContextAdmissionEvent, ...]],
        AdmissionReplay,
    ]


def reduce_context_admission(
    state: ContextAdmissionState,
    event: ContextAdmissionEvent,
) -> AdmissionTransition:
    """Apply one protocol event to the complete prior immutable state."""
    preflight = _preflight(state, event)
    if preflight is not None:
        return preflight
    match event:
        case OpenEpochEvent():
            return _open_epoch(state, event)
        case AuthorityUnavailableEvent():
            kind = (
                AdmissionDecisionKind.UPSTREAM_GATED
                if event.authority_state is CoverageState.UPSTREAM_GATED
                else AdmissionDecisionKind.WATERMARK_UNAVAILABLE
            )
            if isinstance(state, ActiveContextAdmissionState):
                revision, sequence = _effect_coordinates(
                    state,
                    capacity_changed=False,
                )
                return _publish(
                    state,
                    state,
                    event,
                    kind=kind,
                    reason_code=event.reason_code,
                    effects=(
                        AuthorityUnavailableEffect(
                            source_event_id=event.event_id,
                            resulting_aggregate_revision=revision,
                            resulting_admission_sequence=sequence,
                            target_id=state.snapshot.window_epoch_id,
                            reason_code=event.reason_code,
                            authority_state=event.authority_state,
                        ),
                    ),
                )
            return _publish(
                state,
                state,
                event,
                kind=kind,
                reason_code=event.reason_code,
            )
        case ProposeOccurrenceEvent():
            return _propose(state, event)
        case ReserveRequestEvent():
            return _reserve(state, event)
        case PrepareBatchEvent():
            return _prepare(state, event)
        case StageHistoryEvent():
            return _stage(state, event)
        case DispatchRequestEvent():
            return _dispatch(state, event)
        case AcceptInputEvent():
            return _accept(state, event)
        case ReleaseNonAdmissionEvent() | RollbackAdmissionEvent():
            return _release_or_rollback(state, event)
        case MarkIndeterminateEvent():
            return _mark_indeterminate(state, event)
        case ResolveIndeterminateAcceptedEvent():
            return _resolve_indeterminate_accepted(state, event)
        case ResolveIndeterminateNonAdmissionEvent() | ResolveIndeterminateRollbackEvent():
            return _release_or_rollback(state, event)
        case StartGenerationEvent():
            return _start_generation(state, event)
        case ReconcileGenerationEvent():
            return _reconcile_generation(state, event)
        case MarkGenerationIndeterminateEvent():
            return _mark_generation_indeterminate(state, event)
        case RequestReconciliationEvent():
            return _request_reconciliation(state, event)
        case ExpireIdempotencyKeyEvent():
            return _expire_idempotency(state, event)
        case RolloverEpochEvent():
            return _rollover(state, event)
        case _ as unreachable:
            assert_never(unreachable)


def replay_context_admission(
    initial_state: ContextAdmissionState,
    events: tuple[ContextAdmissionEvent, ...],
) -> AdmissionReplay:
    """Replay a full stream, feeding each complete next state into the next event."""
    state = initial_state
    transitions: list[AdmissionTransition] = []
    for event in events:
        transition = reduce_context_admission(state, event)
        transitions.append(transition)
        state = transition.next_state
    return AdmissionReplay(final_state=state, transitions=tuple(transitions))


_CONTEXT_ADMISSION_REDUCER_V1 = ContextAdmissionReducerDef(
    protocol_version=1,
    reduce_transition=reduce_context_admission,
    replay_stream=replay_context_admission,
)
CONTEXT_ADMISSION_REDUCER_REGISTRY: Mapping[int, ContextAdmissionReducerDef] = MappingProxyType(
    {_CONTEXT_ADMISSION_REDUCER_V1.protocol_version: _CONTEXT_ADMISSION_REDUCER_V1}
)


def context_admission_reducer_for_protocol(
    protocol_version: int,
) -> ContextAdmissionReducerDef:
    """Select exactly one released reducer definition."""
    if not isinstance(protocol_version, int) or isinstance(protocol_version, bool):
        raise UnsupportedContextAdmissionProtocolError("unsupported_protocol_version")
    reducer = CONTEXT_ADMISSION_REDUCER_REGISTRY.get(protocol_version)
    if reducer is None:
        raise UnsupportedContextAdmissionProtocolError("unsupported_protocol_version")
    return reducer


def resolve_context_admission_coverage(
    surface: ProducerSurface,
    backend: str,
    configuration_mode: str,
    source_version: str,
    as_of: str,
) -> ProducerCoverageDef:
    """Resolve one static coverage row against runtime lineage inputs."""
    surface_rows = tuple(item for item in CONTEXT_ADMISSION_COVERAGE if item.surface is surface)
    if not surface_rows:
        raise ContextAdmissionValidationError("unknown_producer_surface")
    default_rows = tuple(
        item for item in surface_rows if item.evidence[0].configuration_mode == "default"
    )
    if len(default_rows) != 1:
        raise ContextAdmissionValidationError("invalid_coverage_default_cardinality")

    exact = next(
        (
            item
            for item in surface_rows
            if item.evidence[0].backend == backend
            and item.evidence[0].configuration_mode == configuration_mode
            and item.evidence[0].tested_version == source_version
            and item.evidence[0].checked_at == as_of
        ),
        None,
    )
    if exact is not None:
        return exact

    configuration_rows = tuple(
        item for item in surface_rows if item.evidence[0].configuration_mode == configuration_mode
    )
    row = next(
        (item for item in configuration_rows if item.evidence[0].backend == backend),
        configuration_rows[0] if configuration_rows else default_rows[0],
    )
    return replace(
        row,
        observation_state=CoverageState.UPSTREAM_GATED,
        authority_state=CoverageState.UPSTREAM_GATED,
        reason_code="coverage-runtime-mismatch",
    )
