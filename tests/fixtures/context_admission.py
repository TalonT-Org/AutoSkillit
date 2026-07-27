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
    DeliveryOccurrenceId,
    DispatchIdentity,
    DispatchRequestEvent,
    ForkOccurrenceId,
    GenerationReservationId,
    GenerationReservationRecord,
    GenerationState,
    IdempotencyNamespace,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    MeasurementKind,
    ModelIdentity,
    ModelItemId,
    OpenEpochEvent,
    PrepareBatchEvent,
    ProducerInstanceId,
    ProducerSurface,
    ProposeOccurrenceEvent,
    ProtectedPoolOwnerId,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    RepresentationBindingId,
    RepresentationBindingWitness,
    RepresentationRevision,
    RequestReconciliationEvent,
    ReserveClass,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    RolloverEpochEvent,
    StageHistoryEvent,
    StartGenerationEvent,
    TokenizerIdentity,
    ToolCallId,
    TurnId,
    UninitializedContextAdmissionState,
    WindowEpochId,
    WitnessKind,
)


def stream_key(
    *,
    current_session: str = "session-root",
    current_agent: str = "agent-root",
    current_thread: str = "thread-root",
    fork: str | None = None,
) -> ContextAdmissionStreamKey:
    return ContextAdmissionStreamKey(
        root_session_id=ContextSessionId("session-root"),
        current_session_id=ContextSessionId(current_session),
        root_agent_id=AgentInstanceId("agent-root"),
        current_agent_id=AgentInstanceId(current_agent),
        root_thread_id=ContextThreadId("thread-root"),
        current_thread_id=ContextThreadId(current_thread),
        fork_occurrence_id=ForkOccurrenceId(fork) if fork is not None else None,
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


def idempotency_namespace(
    operation_kind: str,
    *,
    caller_scope: str = "ledger-fixture",
) -> IdempotencyNamespace:
    return IdempotencyNamespace(
        caller_scope=caller_scope,
        operation_kind=operation_kind,
    )


def event_fields(
    state: ContextAdmissionState,
    event_id: str,
    operation_kind: str,
    *,
    expected_revision: int | None = None,
    caller_scope: str = "ledger-fixture",
) -> dict[str, Any]:
    return {
        "event_id": AdmissionEventId(event_id),
        "protocol_version": CONTEXT_ADMISSION_PROTOCOL_VERSION,
        "idempotency_namespace": idempotency_namespace(
            operation_kind,
            caller_scope=caller_scope,
        ),
        "expected_aggregate_revision": AggregateRevision(
            state.aggregate_revision.value if expected_revision is None else expected_revision
        ),
    }


def snapshot(
    *,
    protocol_version: int = CONTEXT_ADMISSION_PROTOCOL_VERSION,
    epoch: int = 1,
    sequence: int = 1,
    active_count: int | None = None,
    hard_limit: int = 100,
    remaining_count: int = 40,
    model: str = "claude-test",
    model_identity: ModelIdentity | None = None,
    tokenizer: str = "tokenizer-test",
    epoch_id: str | None = None,
) -> ContextWindowSnapshot:
    resolved_epoch_id = epoch_id or (
        "epoch-one" if epoch == 1 else "epoch-two" if epoch == 2 else f"epoch-{epoch}"
    )
    return ContextWindowSnapshot(
        protocol_version=protocol_version,
        window_epoch_id=WindowEpochId(resolved_epoch_id),
        window_epoch_number=epoch,
        model_identity=model_identity or ModelIdentity.anthropic(model),
        tokenizer_identity=TokenizerIdentity(tokenizer),
        snapshot_sequence=sequence,
        active_count=hard_limit - remaining_count if active_count is None else active_count,
        hard_limit=hard_limit,
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


def lineage(
    name: str,
    *,
    epoch: int = 1,
    surface: ProducerSurface = ProducerSurface.TOOL_RESULT_ENVELOPE,
    parent_agent: str | None = None,
    fork: str | None = None,
    delivery: str | None = None,
    epoch_id: str | None = None,
    current_session: str | None = None,
    current_agent: str | None = None,
    current_thread: str | None = None,
    dispatch_identity: DispatchIdentity | None = None,
    turn: str | None = None,
) -> ContextLineage:
    resolved_epoch_id = epoch_id or (
        "epoch-one" if epoch == 1 else "epoch-two" if epoch == 2 else f"epoch-{epoch}"
    )
    return ContextLineage(
        root_session_id=ContextSessionId("session-root"),
        current_session_id=ContextSessionId(
            current_session or ("session-child" if parent_agent is not None else "session-root")
        ),
        root_agent_id=AgentInstanceId("agent-root"),
        current_agent_id=AgentInstanceId(
            current_agent or (f"agent-{name}" if parent_agent is not None else "agent-root")
        ),
        parent_agent_id=AgentInstanceId(parent_agent) if parent_agent is not None else None,
        root_thread_id=ContextThreadId("thread-root"),
        current_thread_id=ContextThreadId(
            current_thread or (f"thread-{name}" if fork is not None else "thread-root")
        ),
        parent_thread_id=ContextThreadId("thread-root") if fork is not None else None,
        fork_occurrence_id=ForkOccurrenceId(fork) if fork is not None else None,
        turn_id=TurnId(turn or f"turn-{name}"),
        producer_surface=surface,
        producer_instance_id=ProducerInstanceId(f"producer-{name}"),
        tool_call_id=ToolCallId(f"tool-{name}"),
        model_item_id=ModelItemId(f"item-{name}"),
        dispatch_identity=dispatch_identity,
        attempt_id=AdmissionAttemptId(f"attempt-{name}"),
        delivery_occurrence_id=DeliveryOccurrenceId(delivery) if delivery is not None else None,
        window_epoch_id=WindowEpochId(resolved_epoch_id),
        window_epoch_number=epoch,
    )


def occurrence(
    name: str = "occurrence-one",
    *,
    maximum: int = 10,
    revision: str | None = None,
    epoch: int = 1,
    reserve_class: ReserveClass = ReserveClass.ORDINARY,
    surface: ProducerSurface = ProducerSurface.TOOL_RESULT_ENVELOPE,
    lineage_value: ContextLineage | None = None,
    epoch_id: str | None = None,
    span_id: str | None = None,
) -> AdmissionOccurrence:
    return AdmissionOccurrence(
        occurrence_id=AdmissionOccurrenceId(name),
        lineage=lineage_value or lineage(name, epoch=epoch, surface=surface, epoch_id=epoch_id),
        reserve_class=reserve_class,
        producer_surface=surface,
        predicted_authoritative_maximum=maximum,
        representation_revision=RepresentationRevision(revision or f"revision-{name}"),
        owned_span_ids=(CanonicalSpanId(span_id or f"span-{name}"),),
    )


def canonical_manifest(
    occurrences: tuple[AdmissionOccurrence, ...],
    *,
    request: str,
    revision: str,
    binding: str,
    assembler: str,
    assembler_witness: str | None = None,
) -> CanonicalRepresentationManifest:
    request_id = AdmissionRequestId(request)
    return CanonicalRepresentationManifest(
        request_id=request_id,
        representation_revision=RepresentationRevision(revision),
        representation_binding_id=RepresentationBindingId(binding),
        span_owners=tuple(
            CanonicalSpanOwner(span_id=span_id, occurrence_id=value.occurrence_id)
            for value in occurrences
            for span_id in value.owned_span_ids
        ),
        assembler_identity=ProducerInstanceId(assembler),
        assembler_witness_id=AdmissionWitnessId(assembler_witness or f"{assembler}-witness"),
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


def batch(
    value: AdmissionOccurrence,
    *,
    batch_id: str = "batch-one",
    request: str = "request-one",
    revision: str = "revision-final",
    reserve_class: ReserveClass | None = None,
    protected_pool_owner: str | None = None,
) -> AdmissionBatch:
    request_id = AdmissionRequestId(request)
    identity_suffix = "one" if request == "request-one" else request
    return AdmissionBatch(
        batch_id=AdmissionBatchId(batch_id),
        request_id=request_id,
        occurrence_ids=(value.occurrence_id,),
        reserve_class=reserve_class or value.reserve_class,
        protected_pool_owner_id=(
            ProtectedPoolOwnerId(protected_pool_owner)
            if protected_pool_owner is not None
            else None
        ),
        manifest=canonical_manifest(
            (value,),
            request=request,
            revision=revision,
            binding=f"binding-{identity_suffix}",
            assembler=f"assembler-{identity_suffix}",
            assembler_witness=f"assembler-witness-{identity_suffix}",
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
    *,
    witness_id: str | None = None,
    epoch: int = 1,
    epoch_id: str | None = None,
    revision: str | None = None,
) -> AdmissionWitness:
    resolved_epoch_id = epoch_id or (
        "epoch-one" if epoch == 1 else "epoch-two" if epoch == 2 else f"epoch-{epoch}"
    )
    return AdmissionWitness(
        witness_id=AdmissionWitnessId(witness_id or f"{kind.value}-witness"),
        kind=kind,
        window_epoch_id=WindowEpochId(resolved_epoch_id),
        window_epoch_number=epoch,
        snapshot_sequence=1,
        request_id=batch_value.request_id,
        batch_id=batch_value.batch_id,
        representation_revision=RepresentationRevision(
            revision or batch_value.manifest.representation_revision.value
        ),
        representation_binding_id=batch_value.manifest.representation_binding_id,
        occurrence_ids=batch_value.occurrence_ids,
        authority_source_id=AuthoritySourceId("authority-test"),
    )


def representation_binding(
    batch_value: AdmissionBatch,
    *,
    revision: str | None = None,
) -> RepresentationBindingWitness:
    bound_revision = RepresentationRevision(
        revision or batch_value.manifest.representation_revision.value
    )
    return RepresentationBindingWitness(
        counted_representation_revision=bound_revision,
        dispatched_representation_revision=bound_revision,
        final_manifest_revision=bound_revision,
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


def release_non_admission_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    event_id: str = "event-release",
) -> ReleaseNonAdmissionEvent:
    return ReleaseNonAdmissionEvent(
        **event_fields(state, event_id, "release-non-admission"),
        batch_id=batch_value.batch_id,
        witness=witness(batch_value, WitnessKind.NON_ADMISSION),
    )


def rollback_admission_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    event_id: str = "event-rollback",
) -> RollbackAdmissionEvent:
    return RollbackAdmissionEvent(
        **event_fields(state, event_id, "rollback-admission"),
        batch_id=batch_value.batch_id,
        witness=witness(batch_value, WitnessKind.ROLLBACK),
    )


def mark_indeterminate_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    event_id: str = "event-mark-indeterminate",
    reason_code: str = "ambiguous-provider-result",
) -> MarkIndeterminateEvent:
    return MarkIndeterminateEvent(
        **event_fields(state, event_id, "mark-indeterminate"),
        batch_id=batch_value.batch_id,
        reason_code=reason_code,
    )


def resolve_indeterminate_accepted_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    exact_charge: int,
    event_id: str = "event-resolve-accepted",
) -> ResolveIndeterminateAcceptedEvent:
    return ResolveIndeterminateAcceptedEvent(
        **event_fields(state, event_id, "resolve-indeterminate-accepted"),
        batch_id=batch_value.batch_id,
        witness=witness(batch_value, WitnessKind.PROVIDER_ACCEPTED),
        final_manifest_revision=batch_value.manifest.representation_revision,
        final_manifest=batch_value.manifest,
        exact_charge=exact_charge,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
        representation_binding_witness=representation_binding(batch_value),
    )


def resolve_indeterminate_non_admission_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    event_id: str = "event-resolve-non-admission",
) -> ResolveIndeterminateNonAdmissionEvent:
    return ResolveIndeterminateNonAdmissionEvent(
        **event_fields(state, event_id, "resolve-indeterminate-non-admission"),
        batch_id=batch_value.batch_id,
        witness=witness(batch_value, WitnessKind.NON_ADMISSION),
    )


def resolve_indeterminate_rollback_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
    *,
    event_id: str = "event-resolve-rollback",
) -> ResolveIndeterminateRollbackEvent:
    return ResolveIndeterminateRollbackEvent(
        **event_fields(state, event_id, "resolve-indeterminate-rollback"),
        batch_id=batch_value.batch_id,
        witness=witness(batch_value, WitnessKind.ROLLBACK),
    )


def mark_generation_indeterminate_event(
    state: ContextAdmissionState,
    *,
    generation_reservation_id: str = "generation-one",
    event_id: str = "event-mark-generation-indeterminate",
    reason_code: str = "stream-disconnected",
) -> MarkGenerationIndeterminateEvent:
    return MarkGenerationIndeterminateEvent(
        **event_fields(state, event_id, "mark-generation-indeterminate"),
        generation_reservation_id=GenerationReservationId(generation_reservation_id),
        reason_code=reason_code,
    )


def request_reconciliation_event(
    state: ContextAdmissionState,
    target_id: AdmissionBatchId | GenerationReservationId,
    *,
    event_id: str = "event-request-reconciliation",
    reason_code: str = "explicit-reconciliation-required",
) -> RequestReconciliationEvent:
    return RequestReconciliationEvent(
        **event_fields(state, event_id, "request-reconciliation"),
        target_id=target_id,
        reason_code=reason_code,
    )


def rollover_event(
    state: ContextAdmissionState,
    batch_value: AdmissionBatch,
) -> RolloverEpochEvent:
    return RolloverEpochEvent(
        **event_fields(state, "event-rollover", "rollover-epoch"),
        witness=witness(batch_value, WitnessKind.EPOCH_ROLLOVER),
        fence_proof=None,
        new_snapshot=ContextWindowSnapshot(
            protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
            window_epoch_id=WindowEpochId("epoch-two"),
            window_epoch_number=2,
            model_identity=ModelIdentity.anthropic("claude-test"),
            tokenizer_identity=TokenizerIdentity("tokenizer-test"),
            snapshot_sequence=1,
            active_count=0,
            hard_limit=100,
            remaining_count=100,
        ),
        protected_pools=(),
    )


__all__ = [
    "accept_event",
    "batch",
    "canonical_manifest",
    "dispatch_event",
    "event_fields",
    "generation_reservation",
    "idempotency_namespace",
    "lineage",
    "mark_generation_indeterminate_event",
    "mark_indeterminate_event",
    "occurrence",
    "open_event",
    "prepare_event",
    "propose_event",
    "reservation",
    "reconcile_generation_event",
    "release_non_admission_event",
    "representation_binding",
    "request_reconciliation_event",
    "reserve_event",
    "resolve_indeterminate_accepted_event",
    "resolve_indeterminate_non_admission_event",
    "resolve_indeterminate_rollback_event",
    "rollback_admission_event",
    "rollover_event",
    "snapshot",
    "stage_event",
    "start_generation_event",
    "stream_key",
    "uninitialized_state",
    "witness",
]
