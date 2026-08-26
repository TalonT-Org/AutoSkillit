"""Pure functions over ContextAdmissionState and ContextAdmissionEvent.

No SQLite or transaction concerns. Used by the apply, recovery, and inspection
paths to inspect journal/stream identity and resolve unresolved work.

Wavefront 1 of #4667.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from autoskillit.core import (
    ActiveContextAdmissionState,
    AdmissionState,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStreamKey,
    ContextLineage,
    GenerationState,
)

from ._storage import _LedgerOpenError


def _state_has_unresolved_work(state: ContextAdmissionState) -> bool:
    if not isinstance(state, ActiveContextAdmissionState):
        return False
    unresolved_admission_states = {
        AdmissionState.RESERVED,
        AdmissionState.PREPARED,
        AdmissionState.HISTORY_STAGED,
        AdmissionState.REQUEST_DISPATCHED,
        AdmissionState.INDETERMINATE,
        AdmissionState.QUARANTINED,
    }
    unresolved_generation_states = {
        GenerationState.RESERVED,
        GenerationState.STREAMING,
        GenerationState.INDETERMINATE,
        GenerationState.QUARANTINED,
    }
    return any(
        record.state in unresolved_admission_states for record in state.batch_records
    ) or any(
        record.state in unresolved_generation_states for record in state.generation_reservations
    )


def _state_retains_event(state: ContextAdmissionState, event_id: str) -> bool:
    return any(record.event_id.value == event_id for record in state.processed_events)


def _validate_event_stream_identity(
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> None:
    for lineage in _iter_lineages(event):
        if (
            lineage.root_session_id != stream_key.root_session_id
            or lineage.current_session_id != stream_key.current_session_id
            or lineage.root_agent_id != stream_key.root_agent_id
            or lineage.current_agent_id != stream_key.current_agent_id
            or lineage.root_thread_id != stream_key.root_thread_id
            or lineage.current_thread_id != stream_key.current_thread_id
            or lineage.fork_occurrence_id != stream_key.fork_occurrence_id
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                "stream-identity-mismatch",
            )


def _iter_lineages(value: object) -> tuple[ContextLineage, ...]:
    lineages: list[ContextLineage] = []

    def visit(item: object) -> None:
        if isinstance(item, ContextLineage):
            lineages.append(item)
            return
        if isinstance(item, tuple | frozenset):
            for child in item:
                visit(child)
            return
        if is_dataclass(item):
            for field_def in fields(item):
                visit(getattr(item, field_def.name))

    visit(value)
    return tuple(lineages)


__all__ = [
    "_state_has_unresolved_work",
    "_state_retains_event",
    "_validate_event_stream_identity",
    "_iter_lineages",
]
