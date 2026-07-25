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
    RepresentationBindingId,
    RepresentationBindingWitness,
    RepresentationRevision,
    RequestReconciliationEvent,
    ReservationInvalidatedEffect,
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
        representation_binding_id=RepresentationBindingId(f"binding-{request}"),
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
        batch_id=batch.batch_id,
        representation_revision=batch.manifest.representation_revision,
        occurrence_ids=batch.occurrence_ids,
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
        authority_source_id=None,
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
        representation_binding_id=batch.manifest.representation_binding_id,
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
        representation_binding_id=batch.manifest.representation_binding_id,
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


def _assert_rejection_unchanged(
    before: ActiveContextAdmissionState,
    after: ActiveContextAdmissionState,
) -> None:
    assert len(after.processed_events) == len(before.processed_events) + 1
    assert replace(after, processed_events=before.processed_events) == before


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
    _assert_rejection_unchanged(
        first_transition.next_state,
        stale_transition.next_state,
    )


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


def test_reserve_rejects_active_generation_reservation_id_reuse() -> None:
    state, _, _, existing_generation = _reserved_batch(
        name="generation-id-owner",
        input_count=5,
        generation_count=5,
    )
    occurrence = _occurrence("generation-id-collision", maximum=5)
    state, _ = _propose(state, occurrence)
    batch = _batch("batch-generation-id-collision", (occurrence,))
    _, event = _reserve(
        state,
        batch,
        (occurrence,),
        input_count=5,
        generation_count=5,
    )
    assert event.generation_reservation is not None
    collision = replace(
        event,
        generation_reservation=replace(
            event.generation_reservation,
            generation_reservation_id=existing_generation.generation_reservation_id,
        ),
    )

    rejected = reduce_context_admission(state, collision)

    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert (
        rejected.decision.reason_code == "generation-reservation-id-reuse-with-changed-descriptor"
    )
    assert isinstance(rejected.next_state, ActiveContextAdmissionState)
    _assert_rejection_unchanged(state, rejected.next_state)


def test_reserve_rejects_generation_reservation_id_retained_in_closed_epoch() -> None:
    state, batch, _, existing_generation = _reserved_batch(
        name="closed-generation-id-owner",
        input_count=5,
        generation_count=5,
    )
    state = _prepare_dispatch(state, batch)
    rolled = _rollover_with_receiver_fence(
        state,
        batch,
        name="closed-generation-id-owner",
    )
    occurrence = _occurrence(
        "closed-generation-id-collision",
        maximum=5,
        epoch=2,
    )
    rolled, _ = _propose(rolled, occurrence)
    new_batch = _batch("batch-closed-generation-id-collision", (occurrence,))
    _, event = _reserve(
        rolled,
        new_batch,
        (occurrence,),
        input_count=5,
        generation_count=5,
    )
    assert event.generation_reservation is not None
    collision = replace(
        event,
        generation_reservation=replace(
            event.generation_reservation,
            generation_reservation_id=existing_generation.generation_reservation_id,
        ),
    )

    rejected = reduce_context_admission(rolled, collision)

    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert (
        rejected.decision.reason_code == "generation-reservation-id-reuse-with-changed-descriptor"
    )
    assert isinstance(rejected.next_state, ActiveContextAdmissionState)
    _assert_rejection_unchanged(rolled, rejected.next_state)


def test_reserve_event_rejects_terminal_generation_record() -> None:
    state = _open_epoch()
    occurrence = _occurrence("terminal-generation-reserve", maximum=10)
    batch = _batch("batch-terminal-generation-reserve", (occurrence,))
    generation = replace(
        _generation_reservation(batch, maximum=5),
        state=GenerationState.RECONCILED,
        exact_terminal_usage=5,
        witness_ids=(AdmissionWitnessId("terminal-generation-witness"),),
        authority_source_id=AuthoritySourceId("terminal-generation-authority"),
    )

    with pytest.raises(
        ContextAdmissionValidationError,
        match="generation_reservation_not_open",
    ):
        ReserveRequestEvent(
            **_event_fields(state, "reserve-terminal-generation", "reserve-request"),
            batch=batch,
            snapshot_sequence=state.snapshot.snapshot_sequence,
            input_reservations=(_reservation(batch, (occurrence,), count=5),),
            generation_reservation=generation,
        )


def test_active_state_rejects_global_capacity_overallocation() -> None:
    state, _, _, _ = _reserved_batch(
        remaining_count=60,
        input_count=25,
        generation_count=20,
        name="global-overallocation",
    )
    impossible_snapshot = replace(
        state.snapshot,
        active_count=56,
        remaining_count=44,
    )

    with pytest.raises(ContextAdmissionValidationError) as exc_info:
        replace(state, snapshot=impossible_snapshot)

    assert "context_capacity_overallocated" in str(exc_info.value)


def test_active_state_requires_dispatched_batch_reservation_owner() -> None:
    state, batch, _, _ = _reserved_batch(name="missing-dispatched-reservation")
    state = _prepare_dispatch(state, batch)
    batch_record = _batch_record(state, batch)

    with pytest.raises(
        ContextAdmissionValidationError,
        match="missing_active_batch_reservation",
    ):
        replace(
            state,
            occurrence_records=tuple(
                replace(record, reservation_id=None)
                if record.batch_id == batch.batch_id
                else record
                for record in state.occurrence_records
            ),
            batch_records=(replace(batch_record, reservation_id=None),),
            reservations=(),
        )


def test_active_state_requires_indeterminate_batch_reservation_owner() -> None:
    state, batch, _, _ = _reserved_batch(name="missing-indeterminate-reservation")
    state = _prepare_dispatch(state, batch)
    marked = reduce_context_admission(
        state,
        MarkIndeterminateEvent(
            **_event_fields(
                state,
                "mark-missing-indeterminate-reservation",
                "mark-indeterminate",
            ),
            batch_id=batch.batch_id,
            reason_code="provider-result-lost",
        ),
    )
    assert isinstance(marked.next_state, ActiveContextAdmissionState)
    state = marked.next_state
    batch_record = _batch_record(state, batch)

    with pytest.raises(
        ContextAdmissionValidationError,
        match="missing_active_batch_reservation",
    ):
        replace(
            state,
            occurrence_records=tuple(
                replace(record, reservation_id=None)
                if record.batch_id == batch.batch_id
                else record
                for record in state.occurrence_records
            ),
            batch_records=(
                replace(
                    batch_record,
                    reservation_id=None,
                    unresolved_input_count=0,
                ),
            ),
            reservations=(),
        )


def _prepare_dispatch(
    state: ActiveContextAdmissionState, batch: AdmissionBatch
) -> ActiveContextAdmissionState:
    batch_record = _batch_record(state, batch)
    assert batch_record.reservation_id is not None
    prepare = PrepareBatchEvent(
        **_event_fields(state, f"prepare-{batch.batch_id.value}", "prepare-batch"),
        batch_id=batch.batch_id,
        representation_revision=batch.manifest.representation_revision,
        representation_binding_id=batch.manifest.representation_binding_id,
        proposed_charge=_input_reservation(state, batch_record.reservation_id).reserved_count,
        measurement_kind=MeasurementKind.TOKENIZER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
    )
    prepared = reduce_context_admission(state, prepare)
    assert prepared.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(prepared.next_state, ActiveContextAdmissionState)
    stage = StageHistoryEvent(
        **_event_fields(
            prepared.next_state,
            f"stage-{batch.batch_id.value}",
            "stage-history",
        ),
        batch_id=batch.batch_id,
        witness=_witness(
            batch,
            WitnessKind.HISTORY_STAGED,
            epoch=prepared.next_state.snapshot.window_epoch_number,
        ),
    )
    staged = reduce_context_admission(prepared.next_state, stage)
    assert staged.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(staged.next_state, ActiveContextAdmissionState)
    dispatch = DispatchRequestEvent(
        **_event_fields(
            staged.next_state,
            f"dispatch-{batch.batch_id.value}",
            "dispatch-request",
        ),
        batch_id=batch.batch_id,
        witness=_witness(
            batch,
            WitnessKind.REQUEST_INCLUDED,
            epoch=staged.next_state.snapshot.window_epoch_number,
        ),
    )
    dispatched = reduce_context_admission(staged.next_state, dispatch)
    assert dispatched.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(dispatched.next_state, ActiveContextAdmissionState)
    return dispatched.next_state


def _rollover_with_receiver_fence(
    state: ActiveContextAdmissionState,
    batch: AdmissionBatch,
    *,
    name: str,
) -> ActiveContextAdmissionState:
    receiver = AuthoritySourceId(f"receiver-{name}")
    next_epoch_number = state.snapshot.window_epoch_number + 1
    next_epoch_id = WindowEpochId(f"epoch-{next_epoch_number}")
    dispatch_count = sum(
        isinstance(record.event, DispatchRequestEvent)
        and record.original_decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        and record.event.witness.window_epoch_id == state.snapshot.window_epoch_id
        and record.event.witness.window_epoch_number == state.snapshot.window_epoch_number
        for record in state.processed_events
    )
    event = RolloverEpochEvent(
        **_event_fields(state, f"rollover-{name}", "rollover-epoch"),
        witness=replace(
            _witness(batch, WitnessKind.EPOCH_ROLLOVER),
            authority_source_id=receiver,
        ),
        fence_proof=EpochFenceProof(
            old_window_epoch_id=state.snapshot.window_epoch_id,
            old_window_epoch_number=state.snapshot.window_epoch_number,
            new_window_epoch_id=next_epoch_id,
            new_window_epoch_number=next_epoch_number,
            receiver_authority_source_id=receiver,
            fence_witness_id=AdmissionWitnessId(f"fence-{name}"),
            highest_admitted_dispatch_sequence=dispatch_count,
        ),
        new_snapshot=_snapshot(epoch=next_epoch_number),
        protected_pools=(),
    )
    transition = reduce_context_admission(state, event)
    assert transition.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(transition.next_state, ActiveContextAdmissionState)
    return transition.next_state


def test_dispatch_and_generation_cannot_skip_required_lifecycle_transitions() -> None:
    state, batch, _, generation = _reserved_batch(name="strict-lifecycle")
    batch_record = _batch_record(state, batch)
    assert batch_record.reservation_id is not None
    prepare = PrepareBatchEvent(
        **_event_fields(state, "prepare-strict-lifecycle", "prepare-batch"),
        batch_id=batch.batch_id,
        representation_revision=batch.manifest.representation_revision,
        representation_binding_id=batch.manifest.representation_binding_id,
        proposed_charge=_input_reservation(state, batch_record.reservation_id).reserved_count,
        measurement_kind=MeasurementKind.TOKENIZER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
    )
    prepared = reduce_context_admission(state, prepare)
    assert isinstance(prepared.next_state, ActiveContextAdmissionState)

    skipped_stage = DispatchRequestEvent(
        **_event_fields(
            prepared.next_state,
            "dispatch-without-history-stage",
            "dispatch-request",
        ),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.REQUEST_INCLUDED),
    )
    rejected_dispatch = reduce_context_admission(prepared.next_state, skipped_stage)
    assert rejected_dispatch.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    _assert_rejection_unchanged(prepared.next_state, rejected_dispatch.next_state)

    stage = StageHistoryEvent(
        **_event_fields(prepared.next_state, "stage-strict-lifecycle", "stage-history"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.HISTORY_STAGED),
    )
    staged = reduce_context_admission(prepared.next_state, stage)
    assert isinstance(staged.next_state, ActiveContextAdmissionState)
    premature_generation = StartGenerationEvent(
        **_event_fields(
            staged.next_state,
            "generation-before-dispatch",
            "start-generation",
        ),
        generation_reservation_id=generation.generation_reservation_id,
        witness=_witness(batch, WitnessKind.REQUEST_INCLUDED),
    )
    rejected_generation = reduce_context_admission(
        staged.next_state,
        premature_generation,
    )
    assert rejected_generation.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    _assert_rejection_unchanged(staged.next_state, rejected_generation.next_state)


def test_input_commit_and_output_reconciliation_remain_distinct_domains() -> None:
    state, batch, _, generation = _reserved_batch()
    state = _prepare_dispatch(state, batch)
    start = StartGenerationEvent(
        **_event_fields(state, "start-generation-lifecycle", "start-generation"),
        generation_reservation_id=generation.generation_reservation_id,
        witness=_witness(batch, WitnessKind.REQUEST_INCLUDED),
    )
    started = reduce_context_admission(state, start)
    assert started.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(started.next_state, ActiveContextAdmissionState)
    accept = AcceptInputEvent(
        **_event_fields(started.next_state, "accept-lifecycle", "accept-input"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
        final_manifest_revision=batch.manifest.representation_revision,
        final_manifest=batch.manifest,
        exact_input_charge=18,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
        representation_binding_witness=_binding(batch),
    )
    accepted = reduce_context_admission(started.next_state, accept)
    assert accepted.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(accepted.next_state, ActiveContextAdmissionState)
    assert _batch_record(accepted.next_state, batch).committed_input_count == 18

    reconcile = ReconcileGenerationEvent(
        **_event_fields(
            accepted.next_state,
            "reconcile-generation-lifecycle",
            "reconcile-generation",
        ),
        generation_reservation_id=generation.generation_reservation_id,
        output_usage_witness=_witness(batch, WitnessKind.OUTPUT_USAGE),
        exact_output_usage=7,
    )
    reconciled = reduce_context_admission(accepted.next_state, reconcile)
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
    _assert_rejection_unchanged(state, rejected.next_state)


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
            representation_binding_id=batch.manifest.representation_binding_id,
            proposed_charge=25,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source=AuthoritySourceId("authority-test"),
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
    _assert_rejection_unchanged(state, rejected.next_state)


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
        _assert_rejection_unchanged(state, rejected.next_state)


@pytest.mark.parametrize(
    "mutation",
    (
        "final-manifest-revision",
        "representation-revision-binding",
        "same-revision-binding-identity",
        "assembler-attestation",
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
        if mutation == "representation-revision-binding"
        else batch.manifest.representation_revision.value
    )
    final_manifest = (
        replace(
            batch.manifest,
            assembler_witness_id=AdmissionWitnessId("mutated-assembler-attestation"),
        )
        if mutation == "assembler-attestation"
        else batch.manifest
    )
    binding = _binding(batch, revision=binding_revision)
    if mutation == "same-revision-binding-identity":
        binding = replace(
            binding,
            representation_binding_id=RepresentationBindingId("mutated-binding-identity"),
        )
    event = AcceptInputEvent(
        **_event_fields(state, f"reject-{mutation}", "accept-input"),
        batch_id=batch.batch_id,
        witness=_witness(batch, witness_kind),
        final_manifest_revision=final_revision,
        final_manifest=final_manifest,
        exact_input_charge=20,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("provider-test"),
        representation_binding_witness=binding,
    )
    rejected = reduce_context_admission(state, event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    _assert_rejection_unchanged(state, rejected.next_state)


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
        final_manifest=batch.manifest,
        exact_input_charge=exact_charge,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("provider-test"),
        representation_binding_witness=_binding(batch),
    )
    quarantined = reduce_context_admission(state, event)
    assert quarantined.decision.kind is AdmissionDecisionKind.QUARANTINED
    assert isinstance(quarantined.next_state, ActiveContextAdmissionState)
    record = _batch_record(quarantined.next_state, batch)
    assert record.committed_input_count == exact_charge
    assert record.state is AdmissionState.QUARANTINED


def test_incomplete_manifest_rejection_is_non_mutating_and_retains_reservation() -> None:
    state = _open_epoch(remaining_count=40)
    occurrence = replace(
        _occurrence("incomplete-manifest", maximum=20),
        owned_span_ids=(
            CanonicalSpanId("span-incomplete-manifest-a"),
            CanonicalSpanId("span-incomplete-manifest-b"),
        ),
    )
    state, _ = _propose(state, occurrence)
    complete_batch = _batch("batch-incomplete-manifest", (occurrence,))
    incomplete_manifest = replace(
        complete_batch.manifest,
        span_owners=complete_batch.manifest.span_owners[:1],
    )
    batch = complete_batch
    reserved, _ = _reserve(state, batch, (occurrence,), input_count=20)
    assert isinstance(reserved.next_state, ActiveContextAdmissionState)
    state = _prepare_dispatch(reserved.next_state, batch)
    event = AcceptInputEvent(
        **_event_fields(state, "accept-incomplete-manifest", "accept-input"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
        final_manifest_revision=batch.manifest.representation_revision,
        final_manifest=incomplete_manifest,
        exact_input_charge=17,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
        representation_binding_witness=_binding(batch),
    )
    rejected_acceptance = reduce_context_admission(state, event)
    assert rejected_acceptance.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert rejected_acceptance.decision.reason_code == "representation-binding-mismatch"
    _assert_rejection_unchanged(state, rejected_acceptance.next_state)
    assert rejected_acceptance.effects == ()

    next_occurrence = _occurrence("after-incomplete-manifest", maximum=24)
    next_state, _ = _propose(state, next_occurrence)
    next_batch = _batch("batch-after-incomplete-manifest", (next_occurrence,))
    rejected, _ = _reserve(
        next_state,
        next_batch,
        (next_occurrence,),
        input_count=24,
    )
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT


def test_authority_mismatch_quarantine_publishes_charge_state_and_quarantine() -> None:
    state, batch, _, _ = _reserved_batch(name="authority-mismatch-effects")
    state = _prepare_dispatch(state, batch)
    event = AcceptInputEvent(
        **_event_fields(state, "accept-authority-mismatch", "accept-input"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
        final_manifest_revision=batch.manifest.representation_revision,
        final_manifest=batch.manifest,
        exact_input_charge=20,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("different-authority"),
        representation_binding_witness=_binding(batch),
    )
    quarantined = reduce_context_admission(state, event)
    assert quarantined.decision.kind is AdmissionDecisionKind.QUARANTINED
    assert isinstance(quarantined.next_state, ActiveContextAdmissionState)
    assert _batch_record(quarantined.next_state, batch).committed_input_count == 20
    assert {type(effect).__name__ for effect in quarantined.effects} == {
        "ChargeCommittedEffect",
        "OccurrenceStateChangedEffect",
        "QuarantineRecordedEffect",
    }


def test_fork_requires_distinct_epoch_and_parent_accepts_only_delivery() -> None:
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
    state, _ = _propose(state, occurrences[0])
    fork_event = ProposeOccurrenceEvent(
        **_event_fields(state, "propose-fork", "propose-occurrence"),
        occurrence=occurrences[1],
    )
    rejected_fork = reduce_context_admission(state, fork_event)
    assert rejected_fork.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert rejected_fork.decision.reason_code == "fork-requires-distinct-epoch"
    assert isinstance(rejected_fork.next_state, ActiveContextAdmissionState)
    state = rejected_fork.next_state
    state, _ = _propose(state, occurrences[2])
    assert {record.occurrence.occurrence_id for record in state.occurrence_records} == {
        occurrences[0].occurrence_id,
        occurrences[2].occurrence_id,
    }
    assert occurrences[1].lineage.current_thread_id != occurrences[0].lineage.current_thread_id
    assert occurrences[2].lineage.delivery_occurrence_id is not None


def test_rollover_invalidates_undispatched_work_and_preserves_closed_audits() -> None:
    state, batch, _, _ = _reserved_batch(name="rollover")
    receiver_authority = AuthoritySourceId("receiver-2")
    proof = EpochFenceProof(
        old_window_epoch_id=WindowEpochId("epoch-1"),
        old_window_epoch_number=1,
        new_window_epoch_id=WindowEpochId("epoch-2"),
        new_window_epoch_number=2,
        receiver_authority_source_id=receiver_authority,
        fence_witness_id=AdmissionWitnessId("fence-1-to-2"),
        highest_admitted_dispatch_sequence=0,
    )
    rollover_witness = replace(
        _witness(batch, WitnessKind.EPOCH_ROLLOVER),
        authority_source_id=receiver_authority,
    )
    event = RolloverEpochEvent(
        **_event_fields(state, "rollover-1-to-2", "rollover-epoch"),
        witness=rollover_witness,
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
        highest_admitted_dispatch_sequence=0,
    )
    second_witness = replace(
        _witness(second_batch, WitnessKind.EPOCH_ROLLOVER, epoch=2),
        authority_source_id=second_proof.receiver_authority_source_id,
    )
    second = RolloverEpochEvent(
        **_event_fields(rolled.next_state, "rollover-2-to-3", "rollover-epoch"),
        witness=second_witness,
        fence_proof=second_proof,
        new_snapshot=_snapshot(epoch=3, model="claude-new", tokenizer="tokenizer-new"),
        protected_pools=(),
    )
    rerolled = reduce_context_admission(rolled.next_state, second)
    assert isinstance(rerolled.next_state, ActiveContextAdmissionState)
    assert len(rerolled.next_state.closed_epochs) == 2
    assert rerolled.next_state.closed_epochs[0] == rolled.next_state.closed_epochs[0]


def test_reserve_rejects_batch_id_retained_in_closed_epoch() -> None:
    state, batch, _, _ = _reserved_batch(name="closed-batch-id")
    rolled = _rollover_with_receiver_fence(state, batch, name="closed-batch-id")
    occurrence = _occurrence("closed-batch-id-new", maximum=5, epoch=2)
    rolled, _ = _propose(rolled, occurrence)
    reused_batch = _batch(batch.batch_id.value, (occurrence,))

    rejected, _ = _reserve(
        rolled,
        reused_batch,
        (occurrence,),
        input_count=5,
        event_id="reserve-closed-batch-id-reused",
    )

    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert rejected.decision.reason_code == "batch-already-reserved"
    assert isinstance(rejected.next_state, ActiveContextAdmissionState)
    _assert_rejection_unchanged(rolled, rejected.next_state)


def test_closed_epoch_occurrence_identity_is_immutable_and_exact_retry_is_idempotent() -> None:
    state, batch, occurrences, _ = _reserved_batch(name="closed-occurrence")
    rolled = _rollover_with_receiver_fence(
        state,
        batch,
        name="closed-occurrence",
    )
    occurrence = occurrences[0]
    exact_retry = ProposeOccurrenceEvent(
        **_event_fields(
            rolled,
            "retry-closed-occurrence",
            "propose-occurrence",
        ),
        occurrence=occurrence,
    )
    replayed = reduce_context_admission(rolled, exact_retry)
    assert replayed.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert replayed.next_state == rolled

    changed_occurrences = (
        replace(
            occurrence,
            predicted_authoritative_maximum=occurrence.predicted_authoritative_maximum + 1,
        ),
        replace(
            occurrence,
            lineage=replace(
                occurrence.lineage,
                window_epoch_id=rolled.snapshot.window_epoch_id,
                window_epoch_number=rolled.snapshot.window_epoch_number,
            ),
        ),
    )
    current = rolled
    for index, changed_occurrence in enumerate(changed_occurrences):
        changed = ProposeOccurrenceEvent(
            **_event_fields(
                current,
                f"reuse-closed-occurrence-{index}",
                "propose-occurrence",
            ),
            occurrence=changed_occurrence,
        )
        rejected = reduce_context_admission(current, changed)
        assert rejected.decision.kind is AdmissionDecisionKind.QUARANTINED
        assert rejected.decision.reason_code == "occurrence-identity-corruption"
        _assert_rejection_unchanged(current, rejected.next_state)
        current = rejected.next_state


def test_rollover_rejects_stale_admitted_dispatch_fence() -> None:
    state, batch, _, _ = _reserved_batch(name="stale-dispatch-fence")
    state = _prepare_dispatch(state, batch)
    receiver_authority = AuthoritySourceId("receiver-authority")
    proof = EpochFenceProof(
        old_window_epoch_id=state.snapshot.window_epoch_id,
        old_window_epoch_number=state.snapshot.window_epoch_number,
        new_window_epoch_id=WindowEpochId("epoch-2"),
        new_window_epoch_number=2,
        receiver_authority_source_id=receiver_authority,
        fence_witness_id=AdmissionWitnessId("stale-dispatch-fence"),
        highest_admitted_dispatch_sequence=0,
    )
    event = RolloverEpochEvent(
        **_event_fields(state, "rollover-stale-dispatch", "rollover-epoch"),
        witness=replace(
            _witness(batch, WitnessKind.EPOCH_ROLLOVER),
            authority_source_id=receiver_authority,
        ),
        fence_proof=proof,
        new_snapshot=_snapshot(epoch=2),
        protected_pools=(),
    )
    rejected = reduce_context_admission(state, event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert isinstance(rejected.next_state, ActiveContextAdmissionState)
    _assert_rejection_unchanged(state, rejected.next_state)


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


@pytest.mark.parametrize(
    ("required_kind", "expected_state"),
    [
        (WitnessKind.NON_ADMISSION, AdmissionState.RELEASED),
        (WitnessKind.ROLLBACK, AdmissionState.ROLLED_BACK),
    ],
)
def test_protected_pool_release_policy_accepts_only_its_configured_resolution(
    required_kind: WitnessKind,
    expected_state: AdmissionState,
) -> None:
    owner = ProtectedPoolOwnerId("policy-owner")
    pool = ProtectedPoolSpec(
        reserve_class=ReserveClass.SYNTHESIS,
        capability_owner_id=owner,
        injected_count=30,
        priority=1,
        required_release_witness_kind=required_kind,
    )
    state = _open_epoch(remaining_count=60, protected_pools=(pool,))
    occurrence = _occurrence(
        "protected-release-policy",
        maximum=10,
        reserve_class=ReserveClass.SYNTHESIS,
    )
    state, _ = _propose(state, occurrence)
    batch = _batch(
        "batch-protected-release-policy",
        (occurrence,),
        reserve_class=ReserveClass.SYNTHESIS,
        protected_owner=owner.value,
    )
    reserved, _ = _reserve(
        state,
        batch,
        (occurrence,),
        input_count=10,
        generation_count=0,
    )
    assert reserved.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(reserved.next_state, ActiveContextAdmissionState)
    state = _prepare_dispatch(reserved.next_state, batch)
    marked = reduce_context_admission(
        state,
        MarkIndeterminateEvent(
            **_event_fields(state, "mark-protected-policy", "mark-indeterminate"),
            batch_id=batch.batch_id,
            reason_code="ambiguous-provider-result",
        ),
    )
    assert isinstance(marked.next_state, ActiveContextAdmissionState)
    state = marked.next_state
    non_admission = ResolveIndeterminateNonAdmissionEvent(
        **_event_fields(state, "resolve-protected-non-admission", "resolve-non-admission"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.NON_ADMISSION),
    )
    rollback = ResolveIndeterminateRollbackEvent(
        **_event_fields(state, "resolve-protected-rollback", "resolve-rollback"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.ROLLBACK),
    )
    allowed_event, rejected_event = (
        (non_admission, rollback)
        if required_kind is WitnessKind.NON_ADMISSION
        else (rollback, non_admission)
    )

    rejected = reduce_context_admission(state, rejected_event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert rejected.decision.reason_code == "protected-release-policy-mismatch"
    _assert_rejection_unchanged(state, rejected.next_state)

    allowed = reduce_context_admission(state, allowed_event)
    assert allowed.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(allowed.next_state, ActiveContextAdmissionState)
    assert _batch_record(allowed.next_state, batch).state is expected_state


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


def test_multiple_charges_aggregate_within_one_protected_pool() -> None:
    state = _open_epoch(remaining_count=100, protected_pools=_protected_pools())
    for name, count in (("synthesis-a", 8), ("synthesis-b", 7)):
        occurrence = _occurrence(name, maximum=count, reserve_class=ReserveClass.SYNTHESIS)
        state, _ = _propose(state, occurrence)
        batch = _batch(
            f"batch-{name}",
            (occurrence,),
            reserve_class=ReserveClass.SYNTHESIS,
            protected_owner="synthesis-owner",
        )
        admitted, _ = _reserve(state, batch, (occurrence,), input_count=count)
        assert admitted.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        assert isinstance(admitted.next_state, ActiveContextAdmissionState)
        state = admitted.next_state

    over_pool = _occurrence(
        "synthesis-over-pool",
        maximum=6,
        reserve_class=ReserveClass.SYNTHESIS,
    )
    state, _ = _propose(state, over_pool)
    over_pool_batch = _batch(
        "batch-synthesis-over-pool",
        (over_pool,),
        reserve_class=ReserveClass.SYNTHESIS,
        protected_owner="synthesis-owner",
    )
    rejected, _ = _reserve(state, over_pool_batch, (over_pool,), input_count=6)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT


def test_reserve_event_rejects_batch_reservation_pool_policy_mismatch() -> None:
    occurrence = _occurrence(
        "synthesis-policy",
        maximum=5,
        reserve_class=ReserveClass.SYNTHESIS,
    )
    batch = _batch(
        "batch-synthesis-policy",
        (occurrence,),
        reserve_class=ReserveClass.SYNTHESIS,
        protected_owner="synthesis-owner",
    )
    reservation = _reservation(batch, (occurrence,), count=5)
    wrong_owner = ProtectedPoolOwnerId("different-owner")
    wrong_reservation = replace(
        reservation,
        key=replace(
            reservation.key,
            protected_pool_owner_id=wrong_owner,
        ),
        protected_pool_owner_id=wrong_owner,
    )
    state = _open_epoch(remaining_count=100, protected_pools=_protected_pools())
    with pytest.raises(ContextAdmissionValidationError):
        ReserveRequestEvent(
            **_event_fields(state, "reserve-policy-mismatch", "reserve-request"),
            batch=batch,
            snapshot_sequence=state.snapshot.snapshot_sequence,
            input_reservations=(wrong_reservation,),
            generation_reservation=None,
        )


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
    replayed = reduce_context_admission(transition.next_state, event)
    assert replayed.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert replayed.next_state == transition.next_state


def test_authority_unavailable_active_event_replay_is_idempotent() -> None:
    state = _open_epoch()
    event = AuthorityUnavailableEvent(
        **_event_fields(state, "authority-active", "authority-unavailable"),
        reason_code="provider-watermark-unavailable",
        authority_state=CoverageState.PARTIAL,
    )
    transition = reduce_context_admission(state, event)
    assert transition.decision.kind is AdmissionDecisionKind.WATERMARK_UNAVAILABLE
    assert isinstance(transition.next_state, ActiveContextAdmissionState)
    replayed = reduce_context_admission(transition.next_state, event)
    assert replayed.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert replayed.next_state == transition.next_state


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


def test_rejected_event_retries_and_changed_reuse_have_replay_semantics() -> None:
    state = _open_epoch()
    rejected_event = PrepareBatchEvent(
        **_event_fields(state, "rejected-event", "prepare-batch"),
        batch_id=AdmissionBatchId("unknown-batch"),
        representation_revision=RepresentationRevision("revision-1"),
        representation_binding_id=RepresentationBindingId("binding-unknown"),
        proposed_charge=1,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
    )
    rejected = reduce_context_admission(state, rejected_event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert isinstance(rejected.next_state, ActiveContextAdmissionState)

    replayed = reduce_context_admission(rejected.next_state, rejected_event)
    assert replayed.decision.kind is AdmissionDecisionKind.NOOP_IDEMPOTENT
    assert replayed.next_state == rejected.next_state

    changed = replace(
        rejected_event,
        batch_id=AdmissionBatchId("different-unknown-batch"),
    )
    conflicted = reduce_context_admission(rejected.next_state, changed)
    assert conflicted.decision.kind is AdmissionDecisionKind.CONFLICT
    assert conflicted.next_state == rejected.next_state


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
    _assert_rejection_unchanged(state, rejected.next_state)


@pytest.mark.parametrize("mark_indeterminate", [False, True])
def test_post_rollover_expiry_cannot_tombstone_retained_work(
    mark_indeterminate: bool,
) -> None:
    state, batch, _, _ = _reserved_batch(name=f"retained-expiry-{mark_indeterminate}")
    state = _prepare_dispatch(state, batch)
    if mark_indeterminate:
        marked = reduce_context_admission(
            state,
            MarkIndeterminateEvent(
                **_event_fields(
                    state,
                    "mark-retained-expiry",
                    "mark-indeterminate",
                ),
                batch_id=batch.batch_id,
                reason_code="ambiguous-provider-result",
            ),
        )
        assert isinstance(marked.next_state, ActiveContextAdmissionState)
        state = marked.next_state
    reservation = state.reservations[0]
    rolled = _rollover_with_receiver_fence(
        state,
        batch,
        name=f"retained-expiry-{mark_indeterminate}",
    )
    retained_before = rolled.closed_epochs[-1]
    expiry = ExpireIdempotencyKeyEvent(
        **_event_fields(
            rolled,
            f"expire-retained-{mark_indeterminate}",
            "expire-idempotency-key",
        ),
        reservation_key=reservation.key,
        expiry_witness=_witness(batch, WitnessKind.IDEMPOTENCY_EXPIRY),
    )
    rejected = reduce_context_admission(rolled, expiry)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert rejected.decision.reason_code == "idempotency-key-not-terminal"
    assert rejected.next_state.expired_idempotency_tombstones == ()
    assert rejected.next_state.closed_epochs[-1] == retained_before
    _assert_rejection_unchanged(rolled, rejected.next_state)


def test_post_rollover_expiry_requires_the_complete_witness_binding() -> None:
    state, batch, _, _ = _reserved_batch(name="expiry-binding")
    reservation = state.reservations[0]
    rolled = _rollover_with_receiver_fence(
        state,
        batch,
        name="expiry-binding",
    )
    valid_witness = _witness(batch, WitnessKind.IDEMPOTENCY_EXPIRY)
    mismatched_witnesses = (
        replace(valid_witness, request_id=AdmissionRequestId("wrong-request")),
        replace(valid_witness, batch_id=AdmissionBatchId("wrong-batch")),
        replace(
            valid_witness,
            representation_revision=RepresentationRevision("wrong-revision"),
        ),
        replace(
            valid_witness,
            representation_binding_id=RepresentationBindingId("wrong-binding"),
        ),
        replace(valid_witness, occurrence_ids=(AdmissionOccurrenceId("wrong-occurrence"),)),
        replace(valid_witness, snapshot_sequence=valid_witness.snapshot_sequence + 1),
    )
    current = rolled
    for index, mismatched_witness in enumerate(mismatched_witnesses):
        expiry = ExpireIdempotencyKeyEvent(
            **_event_fields(
                current,
                f"expire-mismatched-binding-{index}",
                "expire-idempotency-key",
            ),
            reservation_key=reservation.key,
            expiry_witness=mismatched_witness,
        )
        rejected = reduce_context_admission(current, expiry)
        assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
        assert rejected.decision.reason_code == "invalid-expiry-witness"
        assert rejected.next_state.expired_idempotency_tombstones == ()
        _assert_rejection_unchanged(current, rejected.next_state)
        current = rejected.next_state

    accepted = reduce_context_admission(
        current,
        ExpireIdempotencyKeyEvent(
            **_event_fields(
                current,
                "expire-complete-binding",
                "expire-idempotency-key",
            ),
            reservation_key=reservation.key,
            expiry_witness=valid_witness,
        ),
    )
    assert accepted.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(accepted.next_state, ActiveContextAdmissionState)
    assert len(accepted.next_state.expired_idempotency_tombstones) == 1

    repeated = reduce_context_admission(
        accepted.next_state,
        ExpireIdempotencyKeyEvent(
            **_event_fields(
                accepted.next_state,
                "expire-complete-binding-again",
                "expire-idempotency-key",
            ),
            reservation_key=reservation.key,
            expiry_witness=valid_witness,
        ),
    )
    assert repeated.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert repeated.decision.reason_code == "idempotency-key-expired"
    assert len(repeated.next_state.expired_idempotency_tombstones) == 1
    _assert_rejection_unchanged(accepted.next_state, repeated.next_state)


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
        final_manifest_revision=batch.manifest.representation_revision,
        final_manifest=batch.manifest,
        exact_charge=19,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("authority-test"),
        representation_binding_witness=_binding(batch),
    )
    resolved = reduce_context_admission(marked.next_state, resolution)
    assert isinstance(resolved.next_state, ActiveContextAdmissionState)
    assert _batch_record(resolved.next_state, batch).committed_input_count == 19
    assert {record.state for record in _records_for(resolved.next_state, batch)} == {
        AdmissionState.COMMITTED
    }


def test_plain_rollback_cannot_resolve_indeterminate_input() -> None:
    state, batch, _, _ = _reserved_batch(name="plain-rollback-indeterminate")
    state = _prepare_dispatch(state, batch)
    marked = reduce_context_admission(
        state,
        MarkIndeterminateEvent(
            **_event_fields(state, "mark-plain-rollback", "mark-indeterminate"),
            batch_id=batch.batch_id,
            reason_code="provider-result-lost",
        ),
    )
    assert isinstance(marked.next_state, ActiveContextAdmissionState)
    event = RollbackAdmissionEvent(
        **_event_fields(marked.next_state, "plain-rollback", "rollback-admission"),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.ROLLBACK),
    )
    rejected = reduce_context_admission(marked.next_state, event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert isinstance(rejected.next_state, ActiveContextAdmissionState)
    _assert_rejection_unchanged(marked.next_state, rejected.next_state)
    assert _batch_record(rejected.next_state, batch).state is AdmissionState.INDETERMINATE


def test_indeterminate_acceptance_requires_matching_authority_source() -> None:
    state, batch, _, _ = _reserved_batch(name="indeterminate-authority")
    state = _prepare_dispatch(state, batch)
    marked = reduce_context_admission(
        state,
        MarkIndeterminateEvent(
            **_event_fields(state, "mark-indeterminate-authority", "mark-indeterminate"),
            batch_id=batch.batch_id,
            reason_code="provider-result-lost",
        ),
    )
    assert isinstance(marked.next_state, ActiveContextAdmissionState)
    event = ResolveIndeterminateAcceptedEvent(
        **_event_fields(
            marked.next_state,
            "resolve-indeterminate-authority",
            "resolve-indeterminate-accepted",
        ),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
        final_manifest_revision=batch.manifest.representation_revision,
        final_manifest=batch.manifest,
        exact_charge=19,
        measurement_kind=MeasurementKind.PROVIDER_EXACT,
        authority_source=AuthoritySourceId("different-authority"),
        representation_binding_witness=_binding(batch),
    )
    rejected = reduce_context_admission(marked.next_state, event)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert isinstance(rejected.next_state, ActiveContextAdmissionState)
    assert _batch_record(rejected.next_state, batch).state is AdmissionState.INDETERMINATE


def test_staged_release_uses_the_exact_single_witness_schema() -> None:
    state, batch, _, _ = _reserved_batch(name="history-removal")
    record = _batch_record(state, batch)
    assert record.reservation_id is not None
    prepared = reduce_context_admission(
        state,
        PrepareBatchEvent(
            **_event_fields(state, "prepare-history-removal", "prepare-batch"),
            batch_id=batch.batch_id,
            representation_revision=batch.manifest.representation_revision,
            representation_binding_id=batch.manifest.representation_binding_id,
            proposed_charge=_input_reservation(
                state,
                record.reservation_id,
            ).reserved_count,
            measurement_kind=MeasurementKind.TOKENIZER_EXACT,
            authority_source=AuthoritySourceId("authority-test"),
        ),
    )
    staged = reduce_context_admission(
        prepared.next_state,
        StageHistoryEvent(
            **_event_fields(
                prepared.next_state,
                "stage-history-removal",
                "stage-history",
            ),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.HISTORY_STAGED),
        ),
    )
    release = ReleaseNonAdmissionEvent(
        **_event_fields(
            staged.next_state,
            "release-with-exact-witness",
            "release-non-admission",
        ),
        batch_id=batch.batch_id,
        witness=_witness(batch, WitnessKind.NON_ADMISSION),
    )
    released = reduce_context_admission(staged.next_state, release)
    assert released.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(released.next_state, ActiveContextAdmissionState)
    assert _batch_record(released.next_state, batch).state is AdmissionState.RELEASED


def test_output_reconciliation_is_bound_to_exact_generation_request() -> None:
    state, batch, _, generation = _reserved_batch(
        name="bound-generation",
        generation_count=8,
    )
    state = _prepare_dispatch(state, batch)
    started = reduce_context_admission(
        state,
        StartGenerationEvent(
            **_event_fields(state, "start-bound-generation", "start-generation"),
            generation_reservation_id=generation.generation_reservation_id,
            witness=_witness(batch, WitnessKind.REQUEST_INCLUDED),
        ),
    )
    other_occurrence = _occurrence("other-output-request", maximum=1)
    other_batch = _batch("batch-other-output-request", (other_occurrence,))
    mismatched = ReconcileGenerationEvent(
        **_event_fields(
            started.next_state,
            "reconcile-cross-request",
            "reconcile-generation",
        ),
        generation_reservation_id=generation.generation_reservation_id,
        output_usage_witness=_witness(other_batch, WitnessKind.OUTPUT_USAGE),
        exact_output_usage=4,
    )
    rejected = reduce_context_admission(started.next_state, mismatched)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
    assert isinstance(rejected.next_state, ActiveContextAdmissionState)
    unchanged = _generation_record(
        rejected.next_state,
        generation.generation_reservation_id,
    )
    assert unchanged.state is GenerationState.STREAMING


def test_release_invalidates_generation_for_exact_batch_not_shared_request() -> None:
    state = _open_epoch()
    first_occurrence = _occurrence("shared-request-first", maximum=10)
    second_occurrence = _occurrence("shared-request-second", maximum=10)
    state, _ = _propose(state, first_occurrence)
    state, _ = _propose(state, second_occurrence)
    first_batch = _batch("batch-shared-request-first", (first_occurrence,))
    second_batch_template = _batch("batch-shared-request-second", (second_occurrence,))
    second_batch = replace(
        second_batch_template,
        request_id=first_batch.request_id,
        manifest=replace(
            second_batch_template.manifest,
            request_id=first_batch.request_id,
        ),
    )
    first_reserved, _ = _reserve(
        state,
        first_batch,
        (first_occurrence,),
        input_count=5,
        generation_count=5,
    )
    assert isinstance(first_reserved.next_state, ActiveContextAdmissionState)
    second_reserved, _ = _reserve(
        first_reserved.next_state,
        second_batch,
        (second_occurrence,),
        input_count=5,
        generation_count=5,
    )
    assert isinstance(second_reserved.next_state, ActiveContextAdmissionState)
    before_release = second_reserved.next_state

    released = reduce_context_admission(
        before_release,
        ReleaseNonAdmissionEvent(
            **_event_fields(before_release, "release-shared-request-first", "release"),
            batch_id=first_batch.batch_id,
            witness=_witness(first_batch, WitnessKind.NON_ADMISSION),
        ),
    )

    assert released.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(released.next_state, ActiveContextAdmissionState)
    assert tuple(record.batch_id for record in released.next_state.generation_reservations) == (
        second_batch.batch_id,
    )
    invalidated_targets = {
        effect.target_id
        for effect in released.effects
        if isinstance(effect, ReservationInvalidatedEffect)
    }
    assert invalidated_targets == {
        GenerationReservationId(f"generation-{first_batch.batch_id.value}")
    }


def test_rollover_moves_old_work_to_resolvable_closed_epoch_audit() -> None:
    state, batch, _, generation = _reserved_batch(
        name="closed-resolution",
        input_count=18,
        generation_count=7,
    )
    state = _prepare_dispatch(state, batch)
    receiver = AuthoritySourceId("receiver-closed-resolution")
    rollover = RolloverEpochEvent(
        **_event_fields(state, "rollover-closed-resolution", "rollover-epoch"),
        witness=replace(
            _witness(batch, WitnessKind.EPOCH_ROLLOVER),
            authority_source_id=receiver,
        ),
        fence_proof=EpochFenceProof(
            old_window_epoch_id=state.snapshot.window_epoch_id,
            old_window_epoch_number=state.snapshot.window_epoch_number,
            new_window_epoch_id=WindowEpochId("epoch-2"),
            new_window_epoch_number=2,
            receiver_authority_source_id=receiver,
            fence_witness_id=AdmissionWitnessId("fence-closed-resolution"),
            highest_admitted_dispatch_sequence=1,
        ),
        new_snapshot=_snapshot(epoch=2),
        protected_pools=(),
    )
    rolled = reduce_context_admission(state, rollover)
    assert rolled.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(rolled.next_state, ActiveContextAdmissionState)
    assert rolled.next_state.batch_records == ()
    assert rolled.next_state.generation_reservations == ()
    audit = rolled.next_state.closed_epochs[-1]
    assert audit.retained_unresolved_count == 18
    assert audit.retained_generation_count == 7

    accepted = reduce_context_admission(
        rolled.next_state,
        AcceptInputEvent(
            **_event_fields(
                rolled.next_state,
                "accept-closed-resolution",
                "accept-input",
            ),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
            final_manifest_revision=batch.manifest.representation_revision,
            final_manifest=batch.manifest,
            exact_input_charge=17,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source=AuthoritySourceId("authority-test"),
            representation_binding_witness=_binding(batch),
        ),
    )
    assert accepted.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(accepted.next_state, ActiveContextAdmissionState)
    accepted_audit = accepted.next_state.closed_epochs[-1]
    assert accepted_audit.retained_unresolved_count == 0
    assert accepted_audit.terminal_batch_records[0].state is AdmissionState.COMMITTED

    reconciled = reduce_context_admission(
        accepted.next_state,
        ReconcileGenerationEvent(
            **_event_fields(
                accepted.next_state,
                "reconcile-closed-generation",
                "reconcile-generation",
            ),
            generation_reservation_id=generation.generation_reservation_id,
            output_usage_witness=_witness(batch, WitnessKind.OUTPUT_USAGE),
            exact_output_usage=5,
        ),
    )
    assert reconciled.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(reconciled.next_state, ActiveContextAdmissionState)
    reconciled_audit = reconciled.next_state.closed_epochs[-1]
    assert reconciled_audit.retained_generation_count == 0
    assert reconciled_audit.terminal_generation_reservations[0].state is GenerationState.RECONCILED


def test_closed_epoch_authority_mismatch_quarantines_exact_charge() -> None:
    state, batch, _, _ = _reserved_batch(
        name="closed-authority-mismatch",
        input_count=18,
        generation_count=0,
    )
    state = _prepare_dispatch(state, batch)
    receiver = AuthoritySourceId("receiver-closed-authority-mismatch")
    rolled = reduce_context_admission(
        state,
        RolloverEpochEvent(
            **_event_fields(state, "rollover-closed-authority-mismatch", "rollover-epoch"),
            witness=replace(
                _witness(batch, WitnessKind.EPOCH_ROLLOVER),
                authority_source_id=receiver,
            ),
            fence_proof=EpochFenceProof(
                old_window_epoch_id=state.snapshot.window_epoch_id,
                old_window_epoch_number=state.snapshot.window_epoch_number,
                new_window_epoch_id=WindowEpochId("epoch-2"),
                new_window_epoch_number=2,
                receiver_authority_source_id=receiver,
                fence_witness_id=AdmissionWitnessId("fence-closed-authority-mismatch"),
                highest_admitted_dispatch_sequence=1,
            ),
            new_snapshot=_snapshot(epoch=2),
            protected_pools=(),
        ),
    )
    assert isinstance(rolled.next_state, ActiveContextAdmissionState)

    quarantined = reduce_context_admission(
        rolled.next_state,
        AcceptInputEvent(
            **_event_fields(
                rolled.next_state,
                "accept-closed-authority-mismatch",
                "accept-input",
            ),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.PROVIDER_ACCEPTED),
            final_manifest_revision=batch.manifest.representation_revision,
            final_manifest=batch.manifest,
            exact_input_charge=20,
            measurement_kind=MeasurementKind.PROVIDER_EXACT,
            authority_source=AuthoritySourceId("different-authority"),
            representation_binding_witness=_binding(batch),
        ),
    )

    assert quarantined.decision.kind is AdmissionDecisionKind.QUARANTINED
    assert quarantined.decision.reason_code == "authority-source-mismatch"
    assert isinstance(quarantined.next_state, ActiveContextAdmissionState)
    audit = quarantined.next_state.closed_epochs[-1]
    assert audit.retained_unresolved_count == 0
    assert audit.terminal_batch_records[0].state is AdmissionState.QUARANTINED
    assert audit.terminal_batch_records[0].committed_input_count == 20
    assert {type(effect).__name__ for effect in quarantined.effects} == {
        "ChargeCommittedEffect",
        "OccurrenceStateChangedEffect",
        "QuarantineRecordedEffect",
    }


def test_protected_release_uses_policy_from_rollover_created_epoch() -> None:
    owner = ProtectedPoolOwnerId("rollover-policy-owner")
    pool = ProtectedPoolSpec(
        reserve_class=ReserveClass.SYNTHESIS,
        capability_owner_id=owner,
        injected_count=20,
        priority=1,
        required_release_witness_kind=WitnessKind.NON_ADMISSION,
    )
    state = _open_epoch(remaining_count=60)
    sentinel = _batch(
        "batch-rollover-policy-sentinel",
        (_occurrence("rollover-policy-sentinel", maximum=1),),
    )
    opened_by_rollover = reduce_context_admission(
        state,
        RolloverEpochEvent(
            **_event_fields(state, "rollover-policy-open", "rollover-epoch"),
            witness=_witness(sentinel, WitnessKind.EPOCH_ROLLOVER),
            fence_proof=None,
            new_snapshot=_snapshot(epoch=2),
            protected_pools=(pool,),
        ),
    )
    assert isinstance(opened_by_rollover.next_state, ActiveContextAdmissionState)
    state = opened_by_rollover.next_state

    occurrence = _occurrence(
        "rollover-policy-work",
        maximum=10,
        reserve_class=ReserveClass.SYNTHESIS,
        epoch=2,
    )
    state, _ = _propose(state, occurrence)
    batch = _batch(
        "batch-rollover-policy-work",
        (occurrence,),
        reserve_class=ReserveClass.SYNTHESIS,
        protected_owner=owner.value,
    )
    _, reserve_event = _reserve(
        state,
        batch,
        (occurrence,),
        input_count=10,
        generation_count=0,
    )
    reserved = reduce_context_admission(
        state,
        replace(reserve_event, generation_reservation=None),
    )
    assert reserved.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(reserved.next_state, ActiveContextAdmissionState)
    state = _prepare_dispatch(reserved.next_state, batch)
    marked = reduce_context_admission(
        state,
        MarkIndeterminateEvent(
            **_event_fields(state, "mark-rollover-policy", "mark-indeterminate"),
            batch_id=batch.batch_id,
            reason_code="ambiguous-provider-result",
        ),
    )
    assert isinstance(marked.next_state, ActiveContextAdmissionState)
    state = marked.next_state

    rolled_again = reduce_context_admission(
        state,
        RolloverEpochEvent(
            **_event_fields(state, "rollover-policy-close", "rollover-epoch"),
            witness=_witness(batch, WitnessKind.EPOCH_ROLLOVER, epoch=2),
            fence_proof=None,
            new_snapshot=_snapshot(epoch=3, active_count=70, remaining_count=30),
            protected_pools=(),
        ),
    )
    assert rolled_again.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(rolled_again.next_state, ActiveContextAdmissionState)
    resolved = reduce_context_admission(
        rolled_again.next_state,
        ResolveIndeterminateNonAdmissionEvent(
            **_event_fields(
                rolled_again.next_state,
                "resolve-rollover-policy",
                "resolve-non-admission",
            ),
            batch_id=batch.batch_id,
            witness=_witness(batch, WitnessKind.NON_ADMISSION, epoch=2),
        ),
    )

    assert resolved.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    assert isinstance(resolved.next_state, ActiveContextAdmissionState)
    assert (
        resolved.next_state.closed_epochs[-1].terminal_batch_records[0].state
        is AdmissionState.RELEASED
    )


def test_rollover_accepts_only_one_explicit_authority_alternative() -> None:
    empty_state = _open_epoch()
    sentinel = _batch(
        "batch-rollover-authority",
        (_occurrence("rollover-authority", maximum=1),),
    )
    fully_resolved = RolloverEpochEvent(
        **_event_fields(empty_state, "rollover-fully-resolved", "rollover-epoch"),
        witness=_witness(sentinel, WitnessKind.EPOCH_ROLLOVER),
        fence_proof=None,
        new_snapshot=_snapshot(epoch=2),
        protected_pools=(),
    )
    resolved_rollover = reduce_context_admission(empty_state, fully_resolved)
    assert resolved_rollover.decision.kind is AdmissionDecisionKind.WOULD_ADMIT

    state, batch, _, _ = _reserved_batch(
        name="snapshot-deduction",
        input_count=10,
        generation_count=0,
    )
    state = _prepare_dispatch(state, batch)
    deducted = RolloverEpochEvent(
        **_event_fields(state, "rollover-snapshot-deducted", "rollover-epoch"),
        witness=_witness(batch, WitnessKind.EPOCH_ROLLOVER),
        fence_proof=None,
        new_snapshot=_snapshot(epoch=2, active_count=70, remaining_count=30),
        protected_pools=(),
    )
    deducted_rollover = reduce_context_admission(state, deducted)
    assert deducted_rollover.decision.kind is AdmissionDecisionKind.WOULD_ADMIT
    invalid_deduction = replace(
        deducted,
        event_id=AdmissionEventId("rollover-invalid-deduction"),
        new_snapshot=_snapshot(epoch=2, active_count=40, remaining_count=60),
    )
    rejected = reduce_context_admission(state, invalid_deduction)
    assert rejected.decision.kind is AdmissionDecisionKind.WOULD_REJECT
