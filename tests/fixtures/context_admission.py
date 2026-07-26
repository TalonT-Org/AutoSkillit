"""Pure deterministic builders for context-admission tests."""

from __future__ import annotations

from typing import Any

from autoskillit.core import (
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    AcceptInputEvent,
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
    AdmissionWitness,
    AdmissionWitnessId,
    AgentInstanceId,
    AggregateRevision,
    AuthoritySourceId,
    CanonicalRepresentationManifest,
    CanonicalSpanId,
    CanonicalSpanOwner,
    ContextAdmissionState,
    ContextAdmissionStreamKey,
    ContextLineage,
    ContextSessionId,
    ContextThreadId,
    ContextWindowSnapshot,
    DispatchRequestEvent,
    GenerationReservationId,
    GenerationReservationRecord,
    GenerationState,
    IdempotencyNamespace,
    MeasurementKind,
    ModelIdentity,
    ModelItemId,
    OpenEpochEvent,
    PrepareBatchEvent,
    ProducerInstanceId,
    ProducerSurface,
    ProposeOccurrenceEvent,
    ReconcileGenerationEvent,
    RepresentationBindingId,
    RepresentationBindingWitness,
    RepresentationRevision,
    ReserveClass,
    ReserveRequestEvent,
    StageHistoryEvent,
    StartGenerationEvent,
    TokenizerIdentity,
    ToolCallId,
    TurnId,
    UninitializedContextAdmissionState,
    WindowEpochId,
    WitnessKind,
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
    generation_allowance: int | None = None,
) -> ReserveRequestEvent:
    return ReserveRequestEvent(
        **event_fields(state, event_id, "reserve-request"),
        batch=batch_value,
        snapshot_sequence=1,
        input_reservations=(reservation(batch_value, occurrence_value, count=count),),
        generation_reservation=(
            generation_reservation(
                batch_value,
                maximum=generation_allowance,
            )
            if generation_allowance is not None
            else None
        ),
    )


def generation_reservation(
    batch_value: AdmissionBatch,
    *,
    maximum: int,
) -> GenerationReservationRecord:
    return GenerationReservationRecord(
        generation_reservation_id=GenerationReservationId("generation-one"),
        request_id=batch_value.request_id,
        batch_id=batch_value.batch_id,
        representation_revision=batch_value.manifest.representation_revision,
        occurrence_ids=batch_value.occurrence_ids,
        response_id=ModelItemId("response-one"),
        window_epoch_id=WindowEpochId("epoch-one"),
        window_epoch_number=1,
        snapshot_sequence=1,
        reserve_class=batch_value.reserve_class,
        protected_pool_owner_id=batch_value.protected_pool_owner_id,
        maximum_allowance=maximum,
        state=GenerationState.RESERVED,
        exact_terminal_usage=None,
        witness_ids=(),
        authority_source_id=None,
    )


def witness(
    batch_value: AdmissionBatch,
    kind: WitnessKind,
) -> AdmissionWitness:
    return AdmissionWitness(
        witness_id=AdmissionWitnessId(f"{kind.value}-witness"),
        kind=kind,
        window_epoch_id=WindowEpochId("epoch-one"),
        window_epoch_number=1,
        snapshot_sequence=1,
        request_id=batch_value.request_id,
        batch_id=batch_value.batch_id,
        representation_revision=batch_value.manifest.representation_revision,
        representation_binding_id=batch_value.manifest.representation_binding_id,
        occurrence_ids=batch_value.occurrence_ids,
        authority_source_id=AuthoritySourceId("authority-test"),
    )


def representation_binding(
    batch_value: AdmissionBatch,
) -> RepresentationBindingWitness:
    return RepresentationBindingWitness(
        counted_representation_revision=batch_value.manifest.representation_revision,
        dispatched_representation_revision=batch_value.manifest.representation_revision,
        final_manifest_revision=batch_value.manifest.representation_revision,
        representation_binding_id=batch_value.manifest.representation_binding_id,
        request_id=batch_value.request_id,
        batch_id=batch_value.batch_id,
        authority_source_id=AuthoritySourceId("authority-test"),
    )


def prepare_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    proposed_charge: int = 10,
) -> PrepareBatchEvent:
    return PrepareBatchEvent(
        **event_fields(state, "event-prepare", "prepare-batch"),
        batch_id=batch_value.batch_id,
        representation_revision=batch_value.manifest.representation_revision,
        representation_binding_id=batch_value.manifest.representation_binding_id,
        proposed_charge=proposed_charge,
        measurement_kind=MeasurementKind.TOKENIZER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
    )


def stage_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
) -> StageHistoryEvent:
    return StageHistoryEvent(
        **event_fields(state, "event-stage", "stage-history"),
        batch_id=batch_value.batch_id,
        witness=witness(batch_value, WitnessKind.HISTORY_STAGED),
    )


def dispatch_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
) -> DispatchRequestEvent:
    return DispatchRequestEvent(
        **event_fields(state, "event-dispatch", "dispatch-request"),
        batch_id=batch_value.batch_id,
        witness=witness(batch_value, WitnessKind.REQUEST_INCLUDED),
    )


def start_generation_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
) -> StartGenerationEvent:
    return StartGenerationEvent(
        **event_fields(state, "event-start-generation", "start-generation"),
        generation_reservation_id=GenerationReservationId("generation-one"),
        witness=witness(batch_value, WitnessKind.REQUEST_INCLUDED),
    )


def accept_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    exact_input_charge: int,
) -> AcceptInputEvent:
    return AcceptInputEvent(
        **event_fields(state, "event-accept", "accept-input"),
        batch_id=batch_value.batch_id,
        witness=witness(batch_value, WitnessKind.PROVIDER_ACCEPTED),
        final_manifest_revision=batch_value.manifest.representation_revision,
        final_manifest=batch_value.manifest,
        exact_input_charge=exact_input_charge,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
        representation_binding_witness=representation_binding(batch_value),
    )


def reconcile_generation_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    exact_output_usage: int,
) -> ReconcileGenerationEvent:
    return ReconcileGenerationEvent(
        **event_fields(state, "event-reconcile-generation", "reconcile-generation"),
        generation_reservation_id=GenerationReservationId("generation-one"),
        output_usage_witness=witness(batch_value, WitnessKind.OUTPUT_USAGE),
        exact_output_usage=exact_output_usage,
    )


__all__ = [
    "accept_event",
    "batch",
    "dispatch_event",
    "event_fields",
    "generation_reservation",
    "lineage",
    "occurrence",
    "open_event",
    "prepare_event",
    "propose_event",
    "reservation",
    "reconcile_generation_event",
    "representation_binding",
    "reserve_event",
    "snapshot",
    "stage_event",
    "start_generation_event",
    "stream_key",
    "uninitialized_state",
    "witness",
]
