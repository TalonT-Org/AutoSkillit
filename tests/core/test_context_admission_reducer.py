"""Behavioral contract for the pure cumulative context-admission reducer."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from autoskillit.core import (
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    AcceptInputEvent,
    ActiveContextAdmissionState,
    AdmissionAttemptId,
    AdmissionBatch,
    AdmissionBatchId,
    AdmissionDecisionKind,
    AdmissionEventId,
    AdmissionOccurrence,
    AdmissionOccurrenceId,
    AdmissionRequestId,
    AdmissionReservation,
    AdmissionReservationId,
    AdmissionReservationKey,
    AdmissionSequence,
    AdmissionState,
    AdmissionWitness,
    AdmissionWitnessId,
    AgentInstanceId,
    AggregateRevision,
    AuthoritySourceId,
    AuthorityUnavailableEvent,
    CanonicalRepresentationManifest,
    CanonicalSpanId,
    CanonicalSpanOwner,
    ContextAdmissionValidationError,
    ContextLineage,
    ContextSessionId,
    ContextThreadId,
    ContextWindowSnapshot,
    CoverageState,
    DeliveryOccurrenceId,
    DispatchRequestEvent,
    EpochFenceProof,
    ExpireIdempotencyKeyEvent,
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
    ProtectedPoolSpec,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
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
    reduce_context_admission,
    replay_context_admission,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _namespace(operation_kind: str) -> IdempotencyNamespace:
    return IdempotencyNamespace(caller_scope="test-caller", operation_kind=operation_kind)


def _event_fields(
    state: UninitializedContextAdmissionState | ActiveContextAdmissionState,
    event_id: str,
    operation_kind: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, object]:
    return {
        "event_id": AdmissionEventId(event_id),
        "protocol_version": CONTEXT_ADMISSION_PROTOCOL_VERSION,
        "idempotency_namespace": _namespace(operation_kind),
        "expected_aggregate_revision": AggregateRevision(
            state.aggregate_revision.value if expected_revision is None else expected_revision
        ),
    }


def _snapshot(
    *,
    epoch: int = 1,
    sequence: int = 1,
    active_count: int = 60,
    hard_limit: int = 100,
    remaining_count: int = 40,
    model: str = "claude-test",
    tokenizer: str = "tokenizer-test",
) -> ContextWindowSnapshot:
    return ContextWindowSnapshot(
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=WindowEpochId(f"epoch-{epoch}"),
        window_epoch_number=epoch,
        model_identity=ModelIdentity.anthropic(model),
        tokenizer_identity=TokenizerIdentity(tokenizer),
        snapshot_sequence=sequence,
        active_count=active_count,
        hard_limit=hard_limit,
        remaining_count=remaining_count,
    )


def _uninitialized() -> UninitializedContextAdmissionState:
    return UninitializedContextAdmissionState(
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        aggregate_revision=AggregateRevision(0),
        admission_sequence=AdmissionSequence(0),
        processed_events=(),
        idempotency_records=(),
        expired_idempotency_tombstones=(),
        closed_epochs=(),
    )


def _open_epoch(
    *,
    remaining_count: int = 40,
    protected_pools: tuple[ProtectedPoolSpec, ...] = (),
    epoch: int = 1,
    state: UninitializedContextAdmissionState | ActiveContextAdmissionState | None = None,
) -> ActiveContextAdmissionState:
    prior = state or _uninitialized()
    transition = reduce_context_admission(
        prior,
        OpenEpochEvent(
            **_event_fields(prior, f"open-{epoch}", "open-epoch"),
            snapshot=_snapshot(
                epoch=epoch,
                active_count=100 - remaining_count,
                remaining_count=remaining_count,
            ),
            protected_pools=protected_pools,
        ),
    )
    assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(transition.next_state, ActiveContextAdmissionState)
    return transition.next_state


def _lineage(
    occurrence: str,
    *,
    epoch: int = 1,
    surface: ProducerSurface = ProducerSurface.TOOL_RESULT_ENVELOPE,
    parent_agent: str | None = None,
    fork: str | None = None,
    delivery: str | None = None,
) -> ContextLineage:
    return ContextLineage(
        root_session_id=ContextSessionId("session-root"),
        current_session_id=ContextSessionId(
            "session-child" if parent_agent is not None else "session-root"
        ),
        root_agent_id=AgentInstanceId("agent-root"),
        current_agent_id=AgentInstanceId(
            f"agent-{occurrence}" if parent_agent is not None else "agent-root"
        ),
        parent_agent_id=AgentInstanceId(parent_agent) if parent_agent is not None else None,
        root_thread_id=ContextThreadId("thread-root"),
        current_thread_id=ContextThreadId(
            f"thread-{occurrence}" if fork is not None else "thread-root"
        ),
        parent_thread_id=ContextThreadId("thread-root") if fork is not None else None,
        fork_occurrence_id=ForkOccurrenceId(fork) if fork is not None else None,
        turn_id=TurnId(f"turn-{occurrence}"),
        producer_surface=surface,
        producer_instance_id=ProducerInstanceId(f"producer-{occurrence}"),
        tool_call_id=ToolCallId(f"tool-{occurrence}"),
        model_item_id=ModelItemId(f"item-{occurrence}"),
        dispatch_identity=None,
        attempt_id=AdmissionAttemptId(f"attempt-{occurrence}"),
        delivery_occurrence_id=(DeliveryOccurrenceId(delivery) if delivery is not None else None),
        window_epoch_id=WindowEpochId(f"epoch-{epoch}"),
        window_epoch_number=epoch,
    )


def _occurrence(
    name: str,
    *,
    maximum: int,
    revision: str | None = None,
    epoch: int = 1,
    reserve_class: ReserveClass = ReserveClass.ORDINARY,
    surface: ProducerSurface = ProducerSurface.TOOL_RESULT_ENVELOPE,
    lineage: ContextLineage | None = None,
) -> AdmissionOccurrence:
    return AdmissionOccurrence(
        occurrence_id=AdmissionOccurrenceId(name),
        lineage=lineage or _lineage(name, epoch=epoch, surface=surface),
        reserve_class=reserve_class,
        producer_surface=surface,
        predicted_authoritative_maximum=maximum,
        representation_revision=RepresentationRevision(revision or f"revision-{name}"),
        owned_span_ids=(CanonicalSpanId(f"span-{name}"),),
    )


def _manifest(
    request: str,
    occurrences: tuple[AdmissionOccurrence, ...],
    *,
    revision: str = "revision-final",
) -> CanonicalRepresentationManifest:
    return CanonicalRepresentationManifest(
        request_id=AdmissionRequestId(request),
        representation_revision=RepresentationRevision(revision),
        span_owners=tuple(
            CanonicalSpanOwner(span_id=span_id, occurrence_id=occurrence.occurrence_id)
            for occurrence in occurrences
            for span_id in occurrence.owned_span_ids
        ),
        assembler_identity=ProducerInstanceId(f"assembler-{request}"),
        assembler_witness_id=AdmissionWitnessId(f"assembler-witness-{request}"),
    )


def _batch(
    name: str,
    occurrences: tuple[AdmissionOccurrence, ...],
    *,
    reserve_class: ReserveClass = ReserveClass.ORDINARY,
    protected_owner: str | None = None,
    manifest_revision: str = "revision-final",
) -> AdmissionBatch:
    request = f"request-{name}"
    return AdmissionBatch(
        batch_id=AdmissionBatchId(name),
        request_id=AdmissionRequestId(request),
        occurrence_ids=tuple(occurrence.occurrence_id for occurrence in occurrences),
        reserve_class=reserve_class,
        protected_pool_owner_id=(
            ProtectedPoolOwnerId(protected_owner) if protected_owner is not None else None
        ),
        manifest=_manifest(request, occurrences, revision=manifest_revision),
    )


def _reservation(
    batch: AdmissionBatch,
    occurrences: tuple[AdmissionOccurrence, ...],
    *,
    count: int,
    snapshot_sequence: int = 1,
) -> AdmissionReservation:
    namespace = _namespace("reserve-request")
    key = AdmissionReservationKey(
        idempotency_namespace=namespace,
        protocol_version=CONTEXT_ADMISSION_PROTOCOL_VERSION,
        window_epoch_id=occurrences[0].lineage.window_epoch_id,
        window_epoch_number=occurrences[0].lineage.window_epoch_number,
        batch_id=batch.batch_id,
        reserve_class=batch.reserve_class,
        protected_pool_owner_id=batch.protected_pool_owner_id,
        occurrence_revisions=tuple(
            (occurrence.occurrence_id, occurrence.representation_revision)
            for occurrence in occurrences
        ),
    )
    return AdmissionReservation(
        reservation_id=AdmissionReservationId(f"reservation-{batch.batch_id.value}"),
        key=key,
        window_epoch_id=occurrences[0].lineage.window_epoch_id,
        window_epoch_number=occurrences[0].lineage.window_epoch_number,
        snapshot_sequence=snapshot_sequence,
        reserve_class=batch.reserve_class,
        protected_pool_owner_id=batch.protected_pool_owner_id,
        occurrence_ids=batch.occurrence_ids,
        reserved_count=count,
    )


def _generation_reservation(
    batch: AdmissionBatch,
    *,
    maximum: int,
    epoch: int = 1,
    snapshot_sequence: int = 1,
) -> GenerationReservationRecord:
    return GenerationReservationRecord(
        generation_reservation_id=GenerationReservationId(f"generation-{batch.batch_id.value}"),
        request_id=batch.request_id,
        response_id=ModelItemId(f"response-{batch.batch_id.value}"),
        window_epoch_id=WindowEpochId(f"epoch-{epoch}"),
        window_epoch_number=epoch,
        snapshot_sequence=snapshot_sequence,
        reserve_class=batch.reserve_class,
        protected_pool_owner_id=batch.protected_pool_owner_id,
        maximum_allowance=maximum,
        state=GenerationState.RESERVED,
        exact_terminal_usage=None,
        witness_ids=(),
    )


def _witness(
    batch: AdmissionBatch,
    kind: WitnessKind,
    *,
    witness: str | None = None,
    revision: str | None = None,
    epoch: int = 1,
) -> AdmissionWitness:
    return AdmissionWitness(
        witness_id=AdmissionWitnessId(witness or f"{kind.value}-witness-{batch.batch_id.value}"),
        kind=kind,
        window_epoch_id=WindowEpochId(f"epoch-{epoch}"),
        window_epoch_number=epoch,
        snapshot_sequence=1,
        request_id=batch.request_id,
        batch_id=batch.batch_id,
        representation_revision=RepresentationRevision(
            revision or batch.manifest.representation_revision.value
        ),
        occurrence_ids=batch.occurrence_ids,
        authority_source_id=AuthoritySourceId("authority-test"),
    )


def _binding(
    batch: AdmissionBatch, *, revision: str | None = None
) -> RepresentationBindingWitness:
    bound_revision = RepresentationRevision(
        revision or batch.manifest.representation_revision.value
    )
    return RepresentationBindingWitness(
        counted_representation_revision=bound_revision,
        dispatched_representation_revision=bound_revision,
        final_manifest_revision=bound_revision,
        request_id=batch.request_id,
        batch_id=batch.batch_id,
        authority_source_id=AuthoritySourceId("authority-test"),
    )


def _propose(
    state: ActiveContextAdmissionState,
    occurrence: AdmissionOccurrence,
    *,
    event_id: str | None = None,
) -> tuple[ActiveContextAdmissionState, ProposeOccurrenceEvent]:
    event = ProposeOccurrenceEvent(
        **_event_fields(
            state,
            event_id or f"propose-{occurrence.occurrence_id.value}",
            "propose-occurrence",
        ),
        occurrence=occurrence,
    )
    transition = reduce_context_admission(state, event)
    assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(transition.next_state, ActiveContextAdmissionState)
    return transition.next_state, event


def _reserve(
    state: ActiveContextAdmissionState,
    batch: AdmissionBatch,
    occurrences: tuple[AdmissionOccurrence, ...],
    *,
    input_count: int,
    generation_count: int = 0,
    event_id: str | None = None,
    expected_revision: int | None = None,
) -> tuple[Any, ReserveRequestEvent]:
    event = ReserveRequestEvent(
        **_event_fields(
            state,
            event_id or f"reserve-{batch.batch_id.value}",
            "reserve-request",
            expected_revision=expected_revision,
        ),
        batch=batch,
        snapshot_sequence=state.snapshot.snapshot_sequence,
        input_reservations=(_reservation(batch, occurrences, count=input_count),),
        generation_reservation=_generation_reservation(batch, maximum=generation_count),
    )
    return reduce_context_admission(state, event), event


def _records_for(state: ActiveContextAdmissionState, batch: AdmissionBatch) -> tuple[Any, ...]:
    wanted = set(batch.occurrence_ids)
    return tuple(
        record for record in state.occurrence_records if record.occurrence.occurrence_id in wanted
    )


def _batch_record(state: ActiveContextAdmissionState, batch: AdmissionBatch) -> Any:
    return next(
        record for record in state.batch_records if record.batch.batch_id == batch.batch_id
    )


def _generation_record(
    state: ActiveContextAdmissionState, reservation_id: GenerationReservationId
) -> Any:
    return next(
        record
        for record in state.generation_reservations
        if record.generation_reservation_id == reservation_id
    )


def _input_reservation(
    state: ActiveContextAdmissionState, reservation_id: AdmissionReservationId
) -> AdmissionReservation:
    return next(
        reservation
        for reservation in state.reservations
        if reservation.reservation_id == reservation_id
    )


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((20, 20, 1), ("WOULD_ADMIT", "WOULD_ADMIT", "WOULD_REJECT")),
        ((39, 1), ("WOULD_ADMIT", "WOULD_ADMIT")),
        ((41,), ("WOULD_REJECT",)),
    ],
)
def test_sequential_occurrences_exhaust_one_authoritative_window(
    counts: tuple[int, ...], expected: tuple[str, ...]
) -> None:
    state = _open_epoch(remaining_count=40)
    decisions: list[str] = []
    for index, count in enumerate(counts):
        occurrence = _occurrence(f"sequential-{index}", maximum=count)
        state, _ = _propose(state, occurrence)
        batch = _batch(f"batch-sequential-{index}", (occurrence,))
        transition, _ = _reserve(state, batch, (occurrence,), input_count=count)
        decisions.append(transition.decision.kind.name)
        assert isinstance(transition.next_state, ActiveContextAdmissionState)
        state = transition.next_state
    assert tuple(decisions) == expected


def test_stale_concurrent_proposals_cannot_overcommit_one_snapshot() -> None:
    state = _open_epoch(remaining_count=30)
    first = _occurrence("concurrent-a", maximum=20)
    second = _occurrence("concurrent-b", maximum=20)
    state, _ = _propose(state, first)
    state, _ = _propose(state, second)
    shared_revision = state.aggregate_revision.value

    first_batch = _batch("batch-concurrent-a", (first,))
    second_batch = _batch("batch-concurrent-b", (second,))
    first_transition, _ = _reserve(
        state,
        first_batch,
        (first,),
        input_count=20,
        expected_revision=shared_revision,
    )
    assert first_transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(first_transition.next_state, ActiveContextAdmissionState)

    stale_transition, _ = _reserve(
        first_transition.next_state,
        second_batch,
        (second,),
        input_count=20,
        expected_revision=shared_revision,
    )
    assert stale_transition.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert stale_transition.next_state == first_transition.next_state


@pytest.mark.parametrize(
    ("remaining", "input_count", "generation_count", "expected_kind"),
    [
        (50, 30, 20, AdmissionDecisionKind.WOULD_ADMIT),
        (49, 30, 20, AdmissionDecisionKind.WOULD_REJECT),
    ],
)
def test_ordered_batch_and_generation_maximum_are_reserved_atomically(
    remaining: int,
    input_count: int,
    generation_count: int,
    expected_kind: AdmissionDecisionKind,
) -> None:
    state = _open_epoch(remaining_count=remaining)
    occurrences = (
        _occurrence("atomic-a", maximum=10),
        _occurrence("atomic-b", maximum=20),
    )
    for occurrence in occurrences:
        state, _ = _propose(state, occurrence)
    batch = _batch("batch-atomic", occurrences)
    transition, _ = _reserve(
        state,
        batch,
        occurrences,
        input_count=input_count,
        generation_count=generation_count,
    )
    assert transition.decision.kind is expected_kind
    assert isinstance(transition.next_state, ActiveContextAdmissionState)
    records = _records_for(transition.next_state, batch)
    assert len(records) == 2
    expected_state = (
        AdmissionState.RESERVED
        if expected_kind is AdmissionDecisionKind.WOULD_ADMIT
        else AdmissionState.PROPOSED
    )
    assert {record.state for record in records} == {expected_state}
    if expected_kind is AdmissionDecisionKind.WOULD_REJECT:
        assert transition.next_state.generation_reservations == ()


def _reserved_batch(
    *,
    remaining_count: int = 60,
    input_count: int = 25,
    generation_count: int = 20,
    name: str = "lifecycle",
) -> tuple[
    ActiveContextAdmissionState,
    AdmissionBatch,
    tuple[AdmissionOccurrence, ...],
    GenerationReservationRecord,
]:
    state = _open_epoch(remaining_count=remaining_count)
    occurrences = (_occurrence(name, maximum=input_count),)
    state, _ = _propose(state, occurrences[0])
    batch = _batch(f"batch-{name}", occurrences)
    transition, _ = _reserve(
        state,
        batch,
        occurrences,
        input_count=input_count,
        generation_count=generation_count,
    )
    assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(transition.next_state, ActiveContextAdmissionState)
    generation = _generation_reservation(batch, maximum=generation_count)
    return transition.next_state, batch, occurrences, generation


def _prepare_dispatch(
    state: ActiveContextAdmissionState, batch: AdmissionBatch
) -> ActiveContextAdmissionState:
    batch_record = _batch_record(state, batch)
    assert batch_record.reservation_id is not None
    prepare = PrepareBatchEvent(
        **_event_fields(state, f"prepare-{batch.batch_id.value}", "prepare-batch"),
        batch_id=batch.batch_id,
        representation_revision=batch.manifest.representation_revision,
        proposed_charge=_input_reservation(state, batch_record.reservation_id).reserved_count,
        measurement_kind=MeasurementKind.TOKENIZER_EXACT,
        authority_source_id=AuthoritySourceId("authority-test"),
    )
    prepared = reduce_context_admission(state, prepare)
    assert prepared.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(prepared.next_state, ActiveContextAdmissionState)
    dispatch = DispatchRequestEvent(
        **_event_fields(
            prepared.next_state,
            f"dispatch-{batch.batch_id.value}",
            "dispatch-request",
        ),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.REQUEST_INCLUDED),
    )
    dispatched = reduce_context_admission(prepared.next_state, dispatch)
    assert dispatched.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(dispatched.next_state, ActiveContextAdmissionState)
    return dispatched.next_state


def test_input_commit_and_output_reconciliation_remain_distinct_domains() -> None:
    state, batch, _, generation = _reserved_batch()
    state = _prepare_dispatch(state, batch)
    accept = AcceptInputEvent(
        **_event_fields(state, "accept-lifecycle", "accept-input"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
        final_manifest_revision=batch.manifest.representation_revision,
        exact_input_charge=18,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source_id=AuthoritySourceId("provider-test"),
        representation_binding_witness=_binding(batch),
    )
    accepted = reduce_context_admission(state, accept)
    assert accepted.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(accepted.next_state, ActiveContextAdmissionState)
    assert _batch_record(accepted.next_state, batch).committed_input_count == 18

    start = StartGenerationEvent(
        **_event_fields(accepted.next_state, "start-generation-lifecycle", "start-generation"),
        generation_reservation_id=generation.generation_reservation_id,
        witness=_witness(batch, WitnessKind.REQUEST_INCLUDED),
    )
    started = reduce_context_admission(accepted.next_state, start)
    assert isinstance(started.next_state, ActiveContextAdmissionState)
    reconcile = ReconcileGenerationEvent(
        **_event_fields(
            started.next_state,
            "reconcile-generation-lifecycle",
            "reconcile-generation",
        ),
        generation_reservation_id=generation.generation_reservation_id,
        output_usage_witness=_witness(batch, WitnessKind.OUTPUT_USAGE),
        exact_output_usage=7,
    )
    reconciled = reduce_context_admission(started.next_state, reconcile)
    assert isinstance(reconciled.next_state, ActiveContextAdmissionState)
    generation_record = _generation_record(
        reconciled.next_state, generation.generation_reservation_id
    )
    assert generation_record.exact_terminal_usage == 7
    assert _batch_record(reconciled.next_state, batch).committed_input_count == 18


def test_overlapping_tool_argument_and_history_span_is_rejected_before_debit() -> None:
    tool_argument = _occurrence(
        "tool-argument",
        maximum=5,
        surface=ProducerSurface.TOOL_ARGUMENT,
    )
    history = replace(
        _occurrence(
            "history",
            maximum=5,
            surface=ProducerSurface.ASSISTANT_OUTPUT_HISTORY,
        ),
        owned_span_ids=tool_argument.owned_span_ids,
    )
    with pytest.raises(ContextAdmissionValidationError):
        _batch("overlapping", (tool_argument, history))


def test_serialized_retry_is_idempotent_and_changed_intent_conflicts() -> None:
    initial = _open_epoch()
    occurrence = _occurrence("idempotent", maximum=7)
    next_state, event = _propose(initial, occurrence, event_id="same-event")
    restored_state = ActiveContextAdmissionState.from_dict(next_state.to_dict())
    restored_event = ProposeOccurrenceEvent.from_dict(event.to_dict())

    retry = reduce_context_admission(restored_state, restored_event)
    assert retry.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert retry.next_state == restored_state

    changed = replace(
        restored_event,
        occurrence=replace(
            occurrence,
            representation_revision=RepresentationRevision("changed-revision"),
        ),
    )
    conflict = reduce_context_admission(restored_state, changed)
    assert conflict.decision.kind is AdmissionDecisionKind.CONFLICT
    assert conflict.next_state == restored_state


def test_same_reservation_key_under_new_delivery_returns_original_result() -> None:
    state = _open_epoch()
    occurrence = _occurrence("new-delivery-retry", maximum=7)
    state, _ = _propose(state, occurrence)
    batch = _batch("batch-new-delivery-retry", (occurrence,))
    reserved, event = _reserve(
        state,
        batch,
        (occurrence,),
        input_count=7,
        generation_count=3,
    )
    assert reserved.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(reserved.next_state, ActiveContextAdmissionState)

    redelivered = replace(event, event_id=AdmissionEventId("reserve-new-delivery-retry-2"))
    retry = reduce_context_admission(reserved.next_state, redelivered)
    assert retry.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert retry.next_state == reserved.next_state

    assert redelivered.generation_reservation is not None
    changed_intent = replace(
        redelivered,
        generation_reservation=replace(
            redelivered.generation_reservation,
            maximum_allowance=4,
        ),
    )
    conflict = reduce_context_admission(reserved.next_state, changed_intent)
    assert conflict.decision.kind is AdmissionDecisionKind.CONFLICT
    assert conflict.next_state == reserved.next_state


@pytest.mark.parametrize(
    ("event_kind", "witness_kind"),
    [
        ("stage", WitnessKind.REQUEST_INCLUDED),
        ("dispatch", WitnessKind.HISTORY_STAGED),
        ("release", WitnessKind.ROLLBACK),
        ("rollback", WitnessKind.NON_ADMISSION),
    ],
)
def test_history_request_provider_and_rollback_witnesses_are_not_interchangeable(
    event_kind: str, witness_kind: WitnessKind
) -> None:
    state, batch, _, _ = _reserved_batch(name=f"witness-{event_kind}")
    if event_kind == "stage":
        event = StageHistoryEvent(
            **_event_fields(state, f"bad-{event_kind}", "stage-history"),
            batch_id=batch.batch_id,
            witness=_witness(batch, witness_kind),
        )
    elif event_kind == "dispatch":
        event = DispatchRequestEvent(
            **_event_fields(state, f"bad-{event_kind}", "dispatch-request"),
            batch_id=batch.batch_id,
            witness=_witness(batch, witness_kind),
        )
    elif event_kind == "release":
        event = ReleaseNonAdmissionEvent(
            **_event_fields(state, f"bad-{event_kind}", "release-non-admission"),
            batch_id=batch.batch_id,
            witness=_witness(batch, witness_kind),
        )
    else:
        event = RollbackAdmissionEvent(
            **_event_fields(state, f"bad-{event_kind}", "rollback-admission"),
            batch_id=batch.batch_id,
            witness=_witness(batch, witness_kind),
        )
    rejected = reduce_context_admission(state, event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert rejected.next_state == state


@pytest.mark.parametrize(
    ("mutation", "expected_reason_fragment"),
    [
        ("count-then-mutate", "revision"),
        ("stale-prepared-revision", "revision"),
        ("stale-receiver-fence", "fence"),
    ],
)
def test_revision_and_fence_failures_do_not_commit(
    mutation: str, expected_reason_fragment: str
) -> None:
    state, batch, _, _ = _reserved_batch(name=mutation)
    if mutation != "stale-receiver-fence":
        event = PrepareBatchEvent(
            **_event_fields(state, f"prepare-{mutation}", "prepare-batch"),
            batch_id=batch.batch_id,
            representation_revision=RepresentationRevision("mutated-revision"),
            proposed_charge=25,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source_id=AuthoritySourceId("authority-test"),
        )
    else:
        event = RolloverEpochEvent(
            **_event_fields(state, "rollover-stale-fence", "rollover-epoch"),
            witness=_witness(batch, WitnessKind.EPOCH_ROLLOVER),
            fence_proof=EpochFenceProof(
                old_window_epoch_id=WindowEpochId("epoch-1"),
                old_window_epoch_number=1,
                new_window_epoch_id=WindowEpochId("epoch-2"),
                new_window_epoch_number=2,
                receiver_authority_source_id=AuthoritySourceId("stale-receiver"),
                fence_witness_id=AdmissionWitnessId("stale-fence"),
                highest_admitted_dispatch_sequence=0,
            ),
            new_snapshot=_snapshot(epoch=2),
            protected_pools=(),
        )
    rejected = reduce_context_admission(state, event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert expected_reason_fragment in rejected.decision.reason_code
    assert rejected.next_state == state


def test_partial_batch_and_illegal_order_are_typed_non_mutating_rejections() -> None:
    state = _open_epoch(remaining_count=40)
    occurrences = (
        _occurrence("partial-a", maximum=10),
        _occurrence("partial-b", maximum=10),
    )
    for occurrence in occurrences:
        state, _ = _propose(state, occurrence)
    batch = _batch("batch-partial", occurrences)
    reserved, _ = _reserve(state, batch, occurrences, input_count=20)
    assert isinstance(reserved.next_state, ActiveContextAdmissionState)
    state = reserved.next_state

    partial_witness = replace(
        _witness(batch, WitnessKind.REQUEST_INCLUDED),
        occurrence_ids=(occurrences[0].occurrence_id,),
    )
    for index, event in enumerate(
        (
            DispatchRequestEvent(
                **_event_fields(
                    state,
                    "illegal-dispatch-before-prepare",
                    "dispatch-request",
                ),
                batch_id=batch.batch_id,
                witness=_witness(batch, WitnessKind.REQUEST_INCLUDED),
            ),
            DispatchRequestEvent(
                **_event_fields(
                    state,
                    "partial-batch-dispatch",
                    "dispatch-request",
                ),
                batch_id=batch.batch_id,
                witness=partial_witness,
            ),
        )
    ):
        rejected = reduce_context_admission(state, event)
        assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT, index
        assert rejected.next_state == state


@pytest.mark.parametrize(
    "mutation",
    (
        "final-manifest-revision",
        "representation-binding",
        "silent-truncation",
    ),
)
def test_acceptance_rejects_mutated_or_unattested_final_representation(
    mutation: str,
) -> None:
    state, batch, _, _ = _reserved_batch(name=f"accept-{mutation}")
    state = _prepare_dispatch(state, batch)
    witness_kind = (
        WitnessKind.TRUNCATION
        if mutation == "silent-truncation"
        else WitnessKind.PROVIDER_ACCEPTED
    )
    final_revision = (
        RepresentationRevision("mutated-final")
        if mutation == "final-manifest-revision"
        else batch.manifest.representation_revision
    )
    binding_revision = (
        "mutated-binding"
        if mutation == "representation-binding"
        else batch.manifest.representation_revision.value
    )
    event = AcceptInputEvent(
        **_event_fields(state, f"reject-{mutation}", "accept-input"),
        batch_id=batch.batch_id,
        witness=_witness(batch, witness_kind),
        final_manifest_revision=final_revision,
        exact_input_charge=20,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source_id=AuthoritySourceId("provider-test"),
        representation_binding_witness=_binding(batch, revision=binding_revision),
    )
    rejected = reduce_context_admission(state, event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert rejected.next_state == state


@pytest.mark.parametrize(
    ("resolve_kind", "terminal_state"),
    [
        ("non-admission", AdmissionState.RELEASED),
        ("rollback", AdmissionState.ROLLED_BACK),
    ],
)
def test_ambiguous_crash_stays_charged_until_an_authoritative_resolution(
    resolve_kind: str, terminal_state: AdmissionState
) -> None:
    state, batch, _, _ = _reserved_batch(name=f"crash-{resolve_kind}")
    state = _prepare_dispatch(state, batch)
    mark = MarkIndeterminateEvent(
        **_event_fields(state, f"mark-{resolve_kind}", "mark-indeterminate"),
        batch_id=batch.batch_id,
        reason_code="ambiguous-crash",
    )
    marked = reduce_context_admission(state, mark)
    assert isinstance(marked.next_state, ActiveContextAdmissionState)
    assert {record.state for record in _records_for(marked.next_state, batch)} == {
        AdmissionState.INDETERMINATE
    }

    query = RequestReconciliationEvent(
        **_event_fields(
            marked.next_state,
            f"query-{resolve_kind}",
            "request-reconciliation",
        ),
        target_id=batch.batch_id,
        reason_code="deadline-observed",
    )
    queried = reduce_context_admission(marked.next_state, query)
    assert isinstance(queried.next_state, ActiveContextAdmissionState)
    queried_batch = _batch_record(queried.next_state, batch)
    assert queried_batch.reservation_id is not None
    assert (
        _input_reservation(queried.next_state, queried_batch.reservation_id).reserved_count == 25
    )
    assert not any("Released" in type(effect).__name__ for effect in queried.effects)

    if resolve_kind == "non-admission":
        resolution = ResolveIndeterminateNonAdmissionEvent(
            **_event_fields(
                queried.next_state,
                f"resolve-{resolve_kind}",
                "resolve-indeterminate-non-admission",
            ),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.NON_ADMISSION),
        )
    else:
        resolution = ResolveIndeterminateRollbackEvent(
            **_event_fields(
                queried.next_state,
                f"resolve-{resolve_kind}",
                "resolve-indeterminate-rollback",
            ),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.ROLLBACK),
        )
    resolved = reduce_context_admission(queried.next_state, resolution)
    assert isinstance(resolved.next_state, ActiveContextAdmissionState)
    assert {record.state for record in _records_for(resolved.next_state, batch)} == {
        terminal_state
    }


@pytest.mark.parametrize("exact_charge", [26, 101])
def test_provider_accepted_overage_is_recorded_and_quarantined(
    exact_charge: int,
) -> None:
    state, batch, _, _ = _reserved_batch(
        remaining_count=100, input_count=25, name=f"overage-{exact_charge}"
    )
    state = _prepare_dispatch(state, batch)
    event = AcceptInputEvent(
        **_event_fields(state, f"accept-overage-{exact_charge}", "accept-input"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
        final_manifest_revision=batch.manifest.representation_revision,
        exact_input_charge=exact_charge,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source_id=AuthoritySourceId("provider-test"),
        representation_binding_witness=_binding(batch),
    )
    quarantined = reduce_context_admission(state, event)
    assert quarantined.decision.kind is AdmissionDecisionKind.QUARANTINED
    assert isinstance(quarantined.next_state, ActiveContextAdmissionState)
    record = _batch_record(quarantined.next_state, batch)
    assert record.committed_input_count == exact_charge
    assert record.state is AdmissionState.QUARANTINED


def test_retry_resume_fork_and_parent_delivery_keep_distinct_occurrences() -> None:
    state = _open_epoch(remaining_count=40)
    lineages = (
        _lineage("resume"),
        _lineage("fork", parent_agent="agent-root", fork="fork-1"),
        _lineage(
            "delivery",
            parent_agent="agent-root",
            fork="fork-1",
            delivery="delivery-1",
            surface=ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY,
        ),
    )
    occurrences = tuple(
        _occurrence(
            name,
            maximum=5,
            lineage=lineage,
            surface=lineage.producer_surface,
        )
        for name, lineage in zip(("resume", "fork", "delivery"), lineages, strict=True)
    )
    for occurrence in occurrences:
        state, _ = _propose(state, occurrence)
    assert {record.occurrence.occurrence_id for record in state.occurrence_records} == {
        occurrence.occurrence_id for occurrence in occurrences
    }
    assert occurrences[1].lineage.current_thread_id != occurrences[0].lineage.current_thread_id
    assert occurrences[2].lineage.delivery_occurrence_id is not None


def test_rollover_invalidates_undispatched_work_and_preserves_closed_audits() -> None:
    state, batch, _, _ = _reserved_batch(name="rollover")
    proof = EpochFenceProof(
        old_window_epoch_id=WindowEpochId("epoch-1"),
        old_window_epoch_number=1,
        new_window_epoch_id=WindowEpochId("epoch-2"),
        new_window_epoch_number=2,
        receiver_authority_source_id=AuthoritySourceId("receiver-2"),
        fence_witness_id=AdmissionWitnessId("fence-1-to-2"),
        highest_admitted_dispatch_sequence=state.admission_sequence.value,
    )
    event = RolloverEpochEvent(
        **_event_fields(state, "rollover-1-to-2", "rollover-epoch"),
        witness=_witness(batch, WitnessKind.EPOCH_ROLLOVER),
        fence_proof=proof,
        new_snapshot=_snapshot(epoch=2),
        protected_pools=(),
    )
    rolled = reduce_context_admission(state, event)
    assert isinstance(rolled.next_state, ActiveContextAdmissionState)
    assert rolled.next_state.snapshot.window_epoch_id == WindowEpochId("epoch-2")
    assert len(rolled.next_state.closed_epochs) == 1
    assert {
        record.state for record in rolled.next_state.closed_epochs[0].terminal_occurrence_records
    } == {AdmissionState.INVALIDATED}

    second_batch = _batch("batch-rollover-2", (_occurrence("rollover-2", maximum=1),))
    second_proof = replace(
        proof,
        old_window_epoch_id=WindowEpochId("epoch-2"),
        old_window_epoch_number=2,
        new_window_epoch_id=WindowEpochId("epoch-3"),
        new_window_epoch_number=3,
        fence_witness_id=AdmissionWitnessId("fence-2-to-3"),
    )
    second = RolloverEpochEvent(
        **_event_fields(rolled.next_state, "rollover-2-to-3", "rollover-epoch"),
        witness=_witness(second_batch, WitnessKind.EPOCH_ROLLOVER, epoch=2),
        fence_proof=second_proof,
        new_snapshot=_snapshot(epoch=3, model="claude-new", tokenizer="tokenizer-new"),
        protected_pools=(),
    )
    rerolled = reduce_context_admission(rolled.next_state, second)
    assert isinstance(rerolled.next_state, ActiveContextAdmissionState)
    assert len(rerolled.next_state.closed_epochs) == 2
    assert rerolled.next_state.closed_epochs[0] == rolled.next_state.closed_epochs[0]


def _protected_pools() -> tuple[ProtectedPoolSpec, ...]:
    return (
        ProtectedPoolSpec(
            reserve_class=ReserveClass.SYNTHESIS,
            capability_owner_id=ProtectedPoolOwnerId("synthesis-owner"),
            injected_count=20,
            priority=10,
            required_release_witness_kind=WitnessKind.NON_ADMISSION,
        ),
        ProtectedPoolSpec(
            reserve_class=ReserveClass.FINAL_RESPONSE,
            capability_owner_id=ProtectedPoolOwnerId("final-owner"),
            injected_count=10,
            priority=20,
            required_release_witness_kind=WitnessKind.NON_ADMISSION,
        ),
    )


def test_protected_pools_are_isolated_without_double_subtracting_usage() -> None:
    state = _open_epoch(remaining_count=100, protected_pools=_protected_pools())
    synthesis = _occurrence("synthesis", maximum=15, reserve_class=ReserveClass.SYNTHESIS)
    state, _ = _propose(state, synthesis)
    synthesis_batch = _batch(
        "batch-synthesis",
        (synthesis,),
        reserve_class=ReserveClass.SYNTHESIS,
        protected_owner="synthesis-owner",
    )
    synthesis_transition, _ = _reserve(
        state,
        synthesis_batch,
        (synthesis,),
        input_count=10,
        generation_count=5,
    )
    assert synthesis_transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(synthesis_transition.next_state, ActiveContextAdmissionState)

    ordinary = _occurrence("ordinary-after-protected", maximum=70)
    state, _ = _propose(synthesis_transition.next_state, ordinary)
    ordinary_batch = _batch("batch-ordinary-after-protected", (ordinary,))
    exact_fit, _ = _reserve(state, ordinary_batch, (ordinary,), input_count=70)
    assert exact_fit.decision.kind is AdmissionDecisionKind.WOULD_ADMIT


@pytest.mark.parametrize(
    ("authority_state", "expected"),
    [
        (CoverageState.PARTIAL, AdmissionDecisionKind.WATERMARK_UNAVAILABLE),
        (CoverageState.UPSTREAM_GATED, AdmissionDecisionKind.UPSTREAM_GATED),
    ],
)
def test_authority_unavailable_never_creates_spendable_capacity(
    authority_state: CoverageState, expected: AdmissionDecisionKind
) -> None:
    state = _uninitialized()
    event = AuthorityUnavailableEvent(
        **_event_fields(state, f"authority-{authority_state.value}", "authority-unavailable"),
        reason_code="no-atomic-watermark",
        authority_state=authority_state,
    )
    transition = reduce_context_admission(state, event)
    assert transition.decision.kind is expected
    assert isinstance(transition.next_state, UninitializedContextAdmissionState)
    assert not hasattr(transition.next_state, "snapshot")


def test_full_stream_replay_uses_each_next_state_as_the_only_next_input() -> None:
    initial = _uninitialized()
    open_event = OpenEpochEvent(
        **_event_fields(initial, "replay-open", "open-epoch"),
        snapshot=_snapshot(),
        protected_pools=(),
    )
    opened = reduce_context_admission(initial, open_event)
    assert isinstance(opened.next_state, ActiveContextAdmissionState)
    occurrence = _occurrence("replay", maximum=10)
    propose_event = ProposeOccurrenceEvent(
        **_event_fields(opened.next_state, "replay-propose", "propose-occurrence"),
        occurrence=occurrence,
    )
    proposed = reduce_context_admission(opened.next_state, propose_event)
    assert isinstance(proposed.next_state, ActiveContextAdmissionState)
    batch = _batch("batch-replay", (occurrence,))
    reserve_event = ReserveRequestEvent(
        **_event_fields(proposed.next_state, "replay-reserve", "reserve-request"),
        batch=batch,
        snapshot_sequence=proposed.next_state.snapshot.snapshot_sequence,
        input_reservations=(_reservation(batch, (occurrence,), count=10),),
        generation_reservation=_generation_reservation(batch, maximum=0),
    )
    sequential = reduce_context_admission(proposed.next_state, reserve_event)
    replay = replay_context_admission(initial, (open_event, propose_event, reserve_event))
    assert replay.final_state == sequential.next_state
    assert replay.transitions[-1] == sequential
    restored = type(replay).from_dict(replay.to_dict())
    assert restored == replay


def test_idempotency_expiry_is_explicit_and_does_not_release_capacity() -> None:
    state, batch, occurrences, _ = _reserved_batch(name="expiry")
    reservation = _reservation(batch, occurrences, count=25)
    event = ExpireIdempotencyKeyEvent(
        **_event_fields(state, "expire-active", "expire-idempotency-key"),
        reservation_key=reservation.key,
        expiry_witness=_witness(batch, WitnessKind.IDEMPOTENCY_EXPIRY),
    )
    rejected = reduce_context_admission(state, event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert rejected.next_state == state


def test_generation_indeterminate_remains_reserved_across_reconciliation_deadline() -> None:
    state, batch, _, generation = _reserved_batch(name="generation-crash")
    event = MarkGenerationIndeterminateEvent(
        **_event_fields(state, "mark-generation-indeterminate", "mark-generation-indeterminate"),
        generation_reservation_id=generation.generation_reservation_id,
        reason_code="stream-disconnected",
    )
    marked = reduce_context_admission(state, event)
    assert isinstance(marked.next_state, ActiveContextAdmissionState)
    record = _generation_record(marked.next_state, generation.generation_reservation_id)
    assert record.state is GenerationState.INDETERMINATE
    assert record.maximum_allowance == 20

    query = RequestReconciliationEvent(
        **_event_fields(
            marked.next_state,
            "query-generation-indeterminate",
            "request-reconciliation",
        ),
        target_id=generation.generation_reservation_id,
        reason_code="deadline-observed",
    )
    queried = reduce_context_admission(marked.next_state, query)
    assert isinstance(queried.next_state, ActiveContextAdmissionState)
    assert (
        _generation_record(
            queried.next_state, generation.generation_reservation_id
        ).maximum_allowance
        == 20
    )
    assert not any("Released" in type(effect).__name__ for effect in queried.effects)


def test_resolve_indeterminate_acceptance_reconciles_exact_charge() -> None:
    state, batch, _, _ = _reserved_batch(name="resolve-accepted")
    state = _prepare_dispatch(state, batch)
    marked = reduce_context_admission(
        state,
        MarkIndeterminateEvent(
            **_event_fields(state, "mark-resolve-accepted", "mark-indeterminate"),
            batch_id=batch.batch_id,
            reason_code="provider-result-lost",
        ),
    )
    assert isinstance(marked.next_state, ActiveContextAdmissionState)
    resolution = ResolveIndeterminateAcceptedEvent(
        **_event_fields(
            marked.next_state,
            "resolve-accepted",
            "resolve-indeterminate-accepted",
        ),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
        exact_charge=19,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source_id=AuthoritySourceId("provider-test"),
    )
    resolved = reduce_context_admission(marked.next_state, resolution)
    assert isinstance(resolved.next_state, ActiveContextAdmissionState)
    assert _batch_record(resolved.next_state, batch).committed_input_count == 19
    assert {record.state for record in _records_for(resolved.next_state, batch)} == {
        AdmissionState.COMMITTED
    }
