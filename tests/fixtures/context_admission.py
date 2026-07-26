"""Pure deterministic builders for context-admission tests."""

from __future__ import annotations

from typing import Any

from autoskillit.core import (
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    AdmissionAttemptId,
    AdmissionBatch,
    AdmissionBatchId,
    AdmissionEventId,
    AdmissionOccurrence,
    AdmissionOccurrenceId,
    AdmissionRequestId,
    AdmissionReservation,
    AdmissionReservationId,
    AdmissionReservationKey,
    AdmissionSequence,
    AdmissionWitnessId,
    AgentInstanceId,
    AggregateRevision,
    CanonicalRepresentationManifest,
    CanonicalSpanId,
    CanonicalSpanOwner,
    ContextAdmissionState,
    ContextAdmissionStreamKey,
    ContextLineage,
    ContextSessionId,
    ContextThreadId,
    ContextWindowSnapshot,
    IdempotencyNamespace,
    ModelIdentity,
    ModelItemId,
    OpenEpochEvent,
    ProducerInstanceId,
    ProducerSurface,
    ProposeOccurrenceEvent,
    RepresentationBindingId,
    RepresentationRevision,
    ReserveClass,
    ReserveRequestEvent,
    TokenizerIdentity,
    ToolCallId,
    TurnId,
    UninitializedContextAdmissionState,
    WindowEpochId,
)


def stream_key() -> ContextAdmissionStreamKey:
    return ContextAdmissionStreamKey(
        root_session_id=ContextSessionId("session-root"),
        current_session_id=ContextSessionId("session-root"),
        root_agent_id=AgentInstanceId("agent-root"),
        current_agent_id=AgentInstanceId("agent-root"),
        root_thread_id=ContextThreadId("thread-root"),
        current_thread_id=ContextThreadId("thread-root"),
        fork_occurrence_id=None,
    )


def uninitialized_state() -> UninitializedContextAdmissionState:
    return UninitializedContextAdmissionState(
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        aggregate_revision=AggregateRevision(0),
        admission_sequence=AdmissionSequence(0),
        processed_events=(),
        idempotency_records=(),
        expired_idempotency_tombstones=(),
        closed_epochs=(),
    )


def event_fields(
    state: ContextAdmissionState,
    event_id: str,
    operation_kind: str,
) -> dict[str, Any]:
    return {
        "event_id": AdmissionEventId(event_id),
        "protocol_version": CONTEXT_ADMISSION_PROTOCOL_VERSION,
        "idempotency_namespace": IdempotencyNamespace(
            caller_scope="ledger-fixture",
            operation_kind=operation_kind,
        ),
        "expected_aggregate_revision": state.aggregate_revision,
    }


def snapshot(*, remaining_count: int = 40) -> ContextWindowSnapshot:
    return ContextWindowSnapshot(
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=WindowEpochId("epoch-one"),
        window_epoch_number=1,
        model_identity=ModelIdentity.anthropic("claude-test"),
        tokenizer_identity=TokenizerIdentity("tokenizer-test"),
        snapshot_sequence=1,
        active_count=100 - remaining_count,
        hard_limit=100,
        remaining_count=remaining_count,
    )


def open_event(
    state: ContextAdmissionState | None = None,
    *,
    event_id: str = "event-open",
    remaining_count: int = 40,
) -> OpenEpochEvent:
    prior = state or uninitialized_state()
    return OpenEpochEvent(
        **event_fields(prior, event_id, "open-epoch"),
        snapshot=snapshot(remaining_count=remaining_count),
        protected_pools=(),
    )


def lineage(name: str) -> ContextLineage:
    return ContextLineage(
        root_session_id=ContextSessionId("session-root"),
        current_session_id=ContextSessionId("session-root"),
        root_agent_id=AgentInstanceId("agent-root"),
        current_agent_id=AgentInstanceId("agent-root"),
        parent_agent_id=None,
        root_thread_id=ContextThreadId("thread-root"),
        current_thread_id=ContextThreadId("thread-root"),
        parent_thread_id=None,
        fork_occurrence_id=None,
        turn_id=TurnId(f"turn-{name}"),
        producer_surface=ProducerSurface.TOOL_RESULT_ENVELOPE,
        producer_instance_id=ProducerInstanceId(f"producer-{name}"),
        tool_call_id=ToolCallId(f"tool-{name}"),
        model_item_id=ModelItemId(f"item-{name}"),
        dispatch_identity=None,
        attempt_id=AdmissionAttemptId(f"attempt-{name}"),
        delivery_occurrence_id=None,
        window_epoch_id=WindowEpochId("epoch-one"),
        window_epoch_number=1,
    )


def occurrence(name: str = "occurrence-one", *, maximum: int = 10) -> AdmissionOccurrence:
    return AdmissionOccurrence(
        occurrence_id=AdmissionOccurrenceId(name),
        lineage=lineage(name),
        reserve_class=ReserveClass.ORDINARY,
        producer_surface=ProducerSurface.TOOL_RESULT_ENVELOPE,
        predicted_authoritative_maximum=maximum,
        representation_revision=RepresentationRevision(f"revision-{name}"),
        owned_span_ids=(CanonicalSpanId(f"span-{name}"),),
    )


def propose_event(
    state: ContextAdmissionState,
    value: AdmissionOccurrence,
    *,
    event_id: str = "event-propose",
) -> ProposeOccurrenceEvent:
    return ProposeOccurrenceEvent(
        **event_fields(state, event_id, "propose-occurrence"),
        occurrence=value,
    )


def batch(value: AdmissionOccurrence) -> AdmissionBatch:
    request_id = AdmissionRequestId("request-one")
    return AdmissionBatch(
        batch_id=AdmissionBatchId("batch-one"),
        request_id=request_id,
        occurrence_ids=(value.occurrence_id,),
        reserve_class=ReserveClass.ORDINARY,
        protected_pool_owner_id=None,
        manifest=CanonicalRepresentationManifest(
            request_id=request_id,
            representation_revision=RepresentationRevision("revision-final"),
            representation_binding_id=RepresentationBindingId("binding-one"),
            span_owners=(
                CanonicalSpanOwner(
                    span_id=value.owned_span_ids[0],
                    occurrence_id=value.occurrence_id,
                ),
            ),
            assembler_identity=ProducerInstanceId("assembler-one"),
            assembler_witness_id=AdmissionWitnessId("assembler-witness-one"),
        ),
    )


def reservation(
    batch_value: AdmissionBatch,
    occurrence_value: AdmissionOccurrence,
    *,
    count: int = 10,
) -> AdmissionReservation:
    namespace = IdempotencyNamespace(
        caller_scope="ledger-fixture",
        operation_kind="reserve-request",
    )
    return AdmissionReservation(
        reservation_id=AdmissionReservationId("reservation-one"),
        key=AdmissionReservationKey(
            idempotency_namespace=namespace,
            protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
            window_epoch_id=WindowEpochId("epoch-one"),
            window_epoch_number=1,
            batch_id=batch_value.batch_id,
            reserve_class=ReserveClass.ORDINARY,
            protected_pool_owner_id=None,
            occurrence_revisions=(
                (
                    occurrence_value.occurrence_id,
                    occurrence_value.representation_revision,
                ),
            ),
        ),
        window_epoch_id=WindowEpochId("epoch-one"),
        window_epoch_number=1,
        snapshot_sequence=1,
        reserve_class=ReserveClass.ORDINARY,
        protected_pool_owner_id=None,
        occurrence_ids=batch_value.occurrence_ids,
        reserved_count=count,
    )


def reserve_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    occurrence_value: AdmissionOccurrence,
    *,
    event_id: str = "event-reserve",
    count: int = 10,
) -> ReserveRequestEvent:
    return ReserveRequestEvent(
        **event_fields(state, event_id, "reserve-request"),
        batch=batch_value,
        snapshot_sequence=1,
        input_reservations=(reservation(batch_value, occurrence_value, count=count),),
        generation_reservation=None,
    )


__all__ = [
    "batch",
    "event_fields",
    "lineage",
    "occurrence",
    "open_event",
    "propose_event",
    "reservation",
    "reserve_event",
    "snapshot",
    "stream_key",
    "uninitialized_state",
]
