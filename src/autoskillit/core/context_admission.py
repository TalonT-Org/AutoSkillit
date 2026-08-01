"""Pure reducer and coverage resolver for cumulative context admission."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import assert_never

from .types._type_context_admission import (
    CONTEXT_ADMISSION_COVERAGE,
    AcceptInputEvent,
    ActiveContextAdmissionState,
    AdmissionBatch,
    AdmissionBatchId,
    AdmissionBatchRecord,
    AdmissionDecision,
    AdmissionEffect,
    AdmissionOccurrenceId,
    AdmissionOccurrenceRecord,
    AdmissionReplay,
    AdmissionReservation,
    AdmissionReservationId,
    AdmissionSequence,
    AdmissionTransition,
    AdmissionWitness,
    AdmissionWitnessId,
    AggregateRevision,
    AuthorityUnavailableEffect,
    AuthorityUnavailableEvent,
    CanonicalSpanId,
    ChargeCommittedEffect,
    ClosedEpochAudit,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionValidationError,
    ContextWindowSnapshot,
    DispatchRequestEvent,
    EpochClosedEffect,
    ExpiredIdempotencyTombstone,
    ExpireIdempotencyKeyEvent,
    GenerationReconciledEffect,
    GenerationReservationId,
    GenerationReservationRecord,
    GenerationReservationRecordedEffect,
    IdempotencyExpiredEffect,
    IdempotencyRecord,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    OccurrenceStateChangedEffect,
    OpenEpochEvent,
    PrepareBatchEvent,
    ProcessedEventRecord,
    ProducerCoverageDef,
    ProducerSurface,
    ProposeOccurrenceEvent,
    ProtectedPoolOwnerId,
    QuarantineRecordedEffect,
    ReconcileGenerationEvent,
    ReconciliationEscalationEffect,
    ReconciliationQueryRequestedEffect,
    ReleaseNonAdmissionEvent,
    RequestReconciliationEvent,
    ReservationInvalidatedEffect,
    ReservationRecordedEffect,
    ReservationReleasedEffect,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    RolloverEpochEvent,
    StageHistoryEvent,
    StartGenerationEvent,
    UninitializedContextAdmissionState,
    UnsupportedContextAdmissionProtocolError,
)
from .types._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    ChargeDomain,
    CoverageState,
    GenerationState,
    MeasurementKind,
    ReserveClass,
    WitnessKind,
)
from .types._type_helpers import _reconciled_snapshot_counts

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


def _effect_coordinates(
    state: ContextAdmissionState,
    *,
    capacity_changed: bool,
) -> tuple[AggregateRevision, AdmissionSequence]:
    return (
        AggregateRevision(state.aggregate_revision.value + 1),
        AdmissionSequence(state.admission_sequence.value + (1 if capacity_changed else 0)),
    )


def _occurrence_effects(
    state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    batch: AdmissionBatch,
    previous_state: AdmissionState,
    next_state: AdmissionState,
    *,
    capacity_changed: bool,
) -> tuple[AdmissionEffect, ...]:
    revision, sequence = _effect_coordinates(
        state,
        capacity_changed=capacity_changed,
    )
    return tuple(
        OccurrenceStateChangedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=occurrence_id,
            previous_state=previous_state,
            next_state=next_state,
        )
        for occurrence_id in batch.occurrence_ids
    )


def _acceptance_effects(
    state: ActiveContextAdmissionState,
    event: AcceptInputEvent | ResolveIndeterminateAcceptedEvent,
    record: AdmissionBatchRecord,
    exact_charge: int,
    witness: AdmissionWitness,
    *,
    quarantine_reason_code: str | None,
) -> tuple[AdmissionEffect, ...]:
    reservation = _reservation_for(state, record)
    if reservation is None:
        return ()
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
    effects: tuple[AdmissionEffect, ...] = (
        ChargeCommittedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=record.batch.batch_id,
            charge_domain=ChargeDomain.INPUT_CONTEXT,
            reserve_class=record.batch.reserve_class,
            protected_pool_owner_id=record.batch.protected_pool_owner_id,
            count=exact_charge,
            window_epoch_id=state.snapshot.window_epoch_id,
            snapshot_sequence=state.snapshot.snapshot_sequence,
            witness_ids=(witness.witness_id,),
        ),
        *_occurrence_effects(
            state,
            event,
            record.batch,
            record.state,
            (
                AdmissionState.QUARANTINED
                if quarantine_reason_code is not None
                else AdmissionState.COMMITTED
            ),
            capacity_changed=True,
        ),
    )
    if quarantine_reason_code is not None:
        effects += (
            QuarantineRecordedEffect(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=record.batch.batch_id,
                reason_code=quarantine_reason_code,
            ),
        )
    return effects


def _accepted_effects(
    state: ActiveContextAdmissionState,
    event: AcceptInputEvent | ResolveIndeterminateAcceptedEvent,
    record: AdmissionBatchRecord,
    exact_charge: int,
    witness: AdmissionWitness,
) -> tuple[AdmissionEffect, ...]:
    reservation = _reservation_for(state, record)
    quarantined = reservation is not None and (
        exact_charge > reservation.reserved_count or exact_charge > state.snapshot.hard_limit
    )
    return _acceptance_effects(
        state,
        event,
        record,
        exact_charge,
        witness,
        quarantine_reason_code=("provider-charge-exceeds-reservation" if quarantined else None),
    )


def _reservation_for(
    state: ActiveContextAdmissionState,
    record: AdmissionBatchRecord,
) -> AdmissionReservation | None:
    if record.reservation_id is None:
        return None
    return next(
        (
            reservation
            for reservation in state.reservations
            if reservation.reservation_id == record.reservation_id
        ),
        None,
    )


def _capacity(
    state: ActiveContextAdmissionState,
) -> tuple[
    int,
    int,
    dict[tuple[ReserveClass, ProtectedPoolOwnerId], int],
]:
    charged_by_pool: dict[tuple[ReserveClass, ProtectedPoolOwnerId], int] = {}
    global_charged = 0

    for record in state.batch_records:
        reservation = _reservation_for(state, record)
        charged = record.charged_input_count(reservation)
        global_charged += charged
        owner = record.batch.protected_pool_owner_id
        if owner is not None:
            key = (record.batch.reserve_class, owner)
            charged_by_pool[key] = charged_by_pool.get(key, 0) + charged

    for generation in state.generation_reservations:
        generation_charge = generation.charged_output_count()
        if generation_charge > 0:
            global_charged += generation_charge
            owner = generation.protected_pool_owner_id
            if owner is not None:
                key = (generation.reserve_class, owner)
                charged_by_pool[key] = charged_by_pool.get(key, 0) + generation_charge

    global_unallocated = max(state.snapshot.remaining_count - global_charged, 0)
    pool_available: dict[tuple[ReserveClass, ProtectedPoolOwnerId], int] = {}
    for pool in state.protected_pools:
        key = (pool.reserve_class, pool.capability_owner_id)
        unused = max(pool.injected_count - charged_by_pool.get(key, 0), 0)
        pool_available[key] = min(unused, global_unallocated)
    ordinary_available = max(
        global_unallocated - sum(pool_available.values()),
        0,
    )
    return global_unallocated, ordinary_available, pool_available


def _decision(
    state: ContextAdmissionState,
    kind: AdmissionDecisionKind,
    reason_code: str,
    *,
    requested_count: int = 0,
    reserve_class: ReserveClass = ReserveClass.ORDINARY,
    protected_pool_owner_id: ProtectedPoolOwnerId | None = None,
) -> AdmissionDecision:
    if isinstance(state, UninitializedContextAdmissionState):
        return AdmissionDecision(
            kind=kind,
            reason_code=reason_code,
            window_epoch_id=None,
            snapshot_sequence=None,
            requested_count=requested_count,
            available_ordinary_count=0,
            available_protected_count=0,
        )
    _, ordinary_available, pool_available = _capacity(state)
    protected_available = 0
    if protected_pool_owner_id is not None:
        protected_available = pool_available.get(
            (reserve_class, protected_pool_owner_id),
            0,
        )
    return AdmissionDecision(
        kind=kind,
        reason_code=reason_code,
        window_epoch_id=state.snapshot.window_epoch_id,
        snapshot_sequence=state.snapshot.snapshot_sequence,
        requested_count=requested_count,
        available_ordinary_count=ordinary_available,
        available_protected_count=protected_available,
    )


def _reject(
    state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    reason_code: str,
    *,
    kind: AdmissionDecisionKind = AdmissionDecisionKind.WOULD_REJECT,
    requested_count: int = 0,
    reserve_class: ReserveClass = ReserveClass.ORDINARY,
    protected_pool_owner_id: ProtectedPoolOwnerId | None = None,
) -> AdmissionTransition:
    decision = _decision(
        state,
        kind,
        reason_code,
        requested_count=requested_count,
        reserve_class=reserve_class,
        protected_pool_owner_id=protected_pool_owner_id,
    )
    processed = ProcessedEventRecord(
        event_id=event.event_id,
        event=event,
        original_decision=decision,
        aggregate_revision=state.aggregate_revision,
        admission_sequence=state.admission_sequence,
    )
    next_state = replace(
        state,
        processed_events=tuple(
            sorted(
                state.processed_events + (processed,),
                key=lambda record: (
                    record.aggregate_revision.value,
                    record.event_id.value,
                ),
            )
        ),
    )
    return AdmissionTransition(
        next_state=next_state,
        decision=decision,
        effects=(),
    )


def _publish(
    prior_state: ContextAdmissionState,
    next_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    *,
    kind: AdmissionDecisionKind = AdmissionDecisionKind.WOULD_ADMIT,
    reason_code: str = "accepted",
    requested_count: int = 0,
    reserve_class: ReserveClass = ReserveClass.ORDINARY,
    protected_pool_owner_id: ProtectedPoolOwnerId | None = None,
    capacity_changed: bool = False,
    effects: tuple[AdmissionEffect, ...] = (),
    idempotency_record: IdempotencyRecord | None = None,
) -> AdmissionTransition:
    aggregate_revision = type(prior_state.aggregate_revision)(
        prior_state.aggregate_revision.value + 1
    )
    admission_sequence = type(prior_state.admission_sequence)(
        prior_state.admission_sequence.value + (1 if capacity_changed else 0)
    )
    published = replace(
        next_state,
        aggregate_revision=aggregate_revision,
        admission_sequence=admission_sequence,
    )
    decision = _decision(
        published,
        kind,
        reason_code,
        requested_count=requested_count,
        reserve_class=reserve_class,
        protected_pool_owner_id=protected_pool_owner_id,
    )
    processed = ProcessedEventRecord(
        event_id=event.event_id,
        event=event,
        original_decision=decision,
        aggregate_revision=aggregate_revision,
        admission_sequence=admission_sequence,
    )
    idempotency_records = published.idempotency_records
    if idempotency_record is not None:
        idempotency_records = tuple(
            sorted(
                idempotency_records + (idempotency_record,),
                key=lambda item: (
                    item.publication_revision.value,
                    item.owning_event_id.value,
                ),
            )
        )
    published = replace(
        published,
        processed_events=tuple(
            sorted(
                published.processed_events + (processed,),
                key=lambda record: (
                    record.aggregate_revision.value,
                    record.event_id.value,
                ),
            )
        ),
        idempotency_records=idempotency_records,
    )
    return AdmissionTransition(
        next_state=published,
        decision=decision,
        effects=effects,
    )


def _preflight(
    state: ContextAdmissionState,
    event: ContextAdmissionEvent,
) -> AdmissionTransition | None:
    prior = next(
        (record for record in state.processed_events if record.event_id == event.event_id),
        None,
    )
    if prior is not None:
        same_event = prior.event == event
        if same_event:
            original = prior.original_decision
            return AdmissionTransition(
                next_state=state,
                decision=AdmissionDecision(
                    kind=AdmissionDecisionKind.NOOP_IDEMPOTENT,
                    reason_code="event-replay",
                    window_epoch_id=original.window_epoch_id,
                    snapshot_sequence=original.snapshot_sequence,
                    requested_count=original.requested_count,
                    available_ordinary_count=original.available_ordinary_count,
                    available_protected_count=original.available_protected_count,
                ),
                effects=(),
            )
        return AdmissionTransition(
            next_state=state,
            decision=_decision(
                state,
                AdmissionDecisionKind.CONFLICT,
                "event-id-conflict",
            ),
            effects=(),
        )
    if isinstance(event, ReserveRequestEvent) and event.input_reservations:
        reservation_key = event.input_reservations[0].key
        if any(
            tombstone.reservation_key == reservation_key
            for tombstone in state.expired_idempotency_tombstones
        ):
            return AdmissionTransition(
                next_state=state,
                decision=_decision(
                    state,
                    AdmissionDecisionKind.IDEMPOTENCY_EXPIRED,
                    "idempotency-expired",
                ),
                effects=(),
            )
        idempotency_record = next(
            (
                record
                for record in state.idempotency_records
                if record.reservation_key == reservation_key
            ),
            None,
        )
        if idempotency_record is not None:
            stored_descriptor = idempotency_record.original_descriptor
            same_intent = (
                stored_descriptor.protocol_version == event.protocol_version
                and stored_descriptor.idempotency_namespace == event.idempotency_namespace
                and stored_descriptor.batch == event.batch
                and stored_descriptor.snapshot_sequence == event.snapshot_sequence
                and stored_descriptor.input_reservations == event.input_reservations
                and stored_descriptor.generation_reservation == event.generation_reservation
            )
            if same_intent:
                original_decision = idempotency_record.original_reserve_decision
                replay_decision = AdmissionDecision(
                    kind=AdmissionDecisionKind.NOOP_IDEMPOTENT,
                    reason_code="reservation-key-replay",
                    window_epoch_id=original_decision.window_epoch_id,
                    snapshot_sequence=original_decision.snapshot_sequence,
                    requested_count=original_decision.requested_count,
                    available_ordinary_count=original_decision.available_ordinary_count,
                    available_protected_count=original_decision.available_protected_count,
                )
                return AdmissionTransition(
                    next_state=state,
                    decision=replay_decision,
                    effects=(),
                )
            return AdmissionTransition(
                next_state=state,
                decision=_decision(
                    state,
                    AdmissionDecisionKind.CONFLICT,
                    "reservation-key-conflict",
                ),
                effects=(),
            )
    if event.expected_aggregate_revision != state.aggregate_revision:
        return _reject(state, event, "stale-revision")
    return None


def _batch_record(
    state: ActiveContextAdmissionState,
    batch_id: AdmissionBatchId,
) -> AdmissionBatchRecord | None:
    return next(
        (record for record in state.batch_records if record.batch.batch_id == batch_id),
        None,
    )


def _highest_dispatch_sequence(state: ActiveContextAdmissionState) -> int:
    return sum(
        isinstance(record.event, DispatchRequestEvent)
        and record.original_decision.kind is AdmissionDecisionKind.WOULD_ADMIT
        and record.event.witness.window_epoch_id == state.snapshot.window_epoch_id
        and record.event.witness.window_epoch_number == state.snapshot.window_epoch_number
        for record in state.processed_events
    )


def _required_release_witness_kind(
    state: ActiveContextAdmissionState,
    batch: AdmissionBatch,
    *,
    snapshot: ContextWindowSnapshot | None = None,
) -> WitnessKind | None:
    if batch.protected_pool_owner_id is None:
        return None
    policy_snapshot = snapshot or state.snapshot
    if (
        policy_snapshot.window_epoch_id == state.snapshot.window_epoch_id
        and policy_snapshot.window_epoch_number == state.snapshot.window_epoch_number
    ):
        protected_pools = state.protected_pools
    else:
        policy_event = next(
            (
                record.event
                for record in state.processed_events
                if (
                    isinstance(record.event, OpenEpochEvent)
                    and record.event.snapshot.window_epoch_id == policy_snapshot.window_epoch_id
                    and record.event.snapshot.window_epoch_number
                    == policy_snapshot.window_epoch_number
                )
                or (
                    isinstance(record.event, RolloverEpochEvent)
                    and record.event.new_snapshot.window_epoch_id
                    == policy_snapshot.window_epoch_id
                    and record.event.new_snapshot.window_epoch_number
                    == policy_snapshot.window_epoch_number
                )
            ),
            None,
        )
        protected_pools = policy_event.protected_pools if policy_event is not None else ()
    pool = next(
        (
            item
            for item in protected_pools
            if item.reserve_class is batch.reserve_class
            and item.capability_owner_id == batch.protected_pool_owner_id
        ),
        None,
    )
    return pool.required_release_witness_kind if pool is not None else None


def _append_witness_ids(
    witness_ids: tuple[AdmissionWitnessId, ...],
    *new_witness_ids: AdmissionWitnessId,
) -> tuple[AdmissionWitnessId, ...]:
    return tuple(
        sorted(
            {*witness_ids, *new_witness_ids},
            key=lambda witness_id: witness_id.value,
        )
    )


def _generation_record(
    state: ActiveContextAdmissionState,
    reservation_id: GenerationReservationId,
) -> GenerationReservationRecord | None:
    return next(
        (
            record
            for record in state.generation_reservations
            if record.generation_reservation_id == reservation_id
        ),
        None,
    )


def _replace_batch_record(
    state: ActiveContextAdmissionState,
    updated: AdmissionBatchRecord,
) -> ActiveContextAdmissionState:
    member_ids = set(updated.batch.occurrence_ids)
    return replace(
        state,
        batch_records=tuple(
            updated if record.batch.batch_id == updated.batch.batch_id else record
            for record in state.batch_records
        ),
        occurrence_records=tuple(
            replace(
                record,
                state=updated.state,
                batch_id=updated.batch.batch_id,
                reservation_id=updated.reservation_id,
            )
            if record.occurrence.occurrence_id in member_ids
            else record
            for record in state.occurrence_records
        ),
    )


def _quarantined_acceptance_state(
    state: ActiveContextAdmissionState,
    record: AdmissionBatchRecord,
    witness: AdmissionWitness,
    exact_charge: int,
    reason_code: str,
) -> ActiveContextAdmissionState:
    quarantined = replace(
        record,
        state=AdmissionState.QUARANTINED,
        witness_ids=_append_witness_ids(record.witness_ids, witness.witness_id),
        committed_input_count=exact_charge,
        unresolved_input_count=0,
    )
    next_state = _replace_batch_record(state, quarantined)
    return _set_occurrence_state(
        next_state,
        record.batch,
        AdmissionState.QUARANTINED,
        witness=witness,
        quarantine_reason_code=reason_code,
    )


def _set_occurrence_state(
    state: ActiveContextAdmissionState,
    batch: AdmissionBatch,
    lifecycle_state: AdmissionState,
    *,
    reservation_id: AdmissionReservationId | None = None,
    witness: AdmissionWitness | None = None,
    indeterminate_reason_code: str | None = None,
    quarantine_reason_code: str | None = None,
) -> ActiveContextAdmissionState:
    member_ids = set(batch.occurrence_ids)
    records: list[AdmissionOccurrenceRecord] = []
    for record in state.occurrence_records:
        if record.occurrence.occurrence_id not in member_ids:
            records.append(record)
            continue
        witness_ids = record.accepted_witness_ids
        if witness is not None and witness.witness_id not in witness_ids:
            witness_ids = _append_witness_ids(witness_ids, witness.witness_id)
        records.append(
            replace(
                record,
                state=lifecycle_state,
                batch_id=batch.batch_id,
                reservation_id=(
                    reservation_id if reservation_id is not None else record.reservation_id
                ),
                accepted_witness_ids=witness_ids,
                indeterminate_reason_code=indeterminate_reason_code,
                quarantine_reason_code=quarantine_reason_code,
            )
        )
    return replace(state, occurrence_records=tuple(records))


def _validate_witness(
    state: ActiveContextAdmissionState,
    batch: AdmissionBatch,
    witness: AdmissionWitness,
    expected_kind: WitnessKind,
) -> bool:
    return _validate_witness_for_snapshot(
        state.snapshot,
        batch,
        witness,
        expected_kind,
    )


def _validate_witness_for_snapshot(
    snapshot: ContextWindowSnapshot,
    batch: AdmissionBatch,
    witness: AdmissionWitness,
    expected_kind: WitnessKind,
) -> bool:
    return (
        witness.kind is expected_kind
        and witness.window_epoch_id == snapshot.window_epoch_id
        and witness.window_epoch_number == snapshot.window_epoch_number
        and witness.snapshot_sequence == snapshot.snapshot_sequence
        and witness.request_id == batch.request_id
        and witness.batch_id == batch.batch_id
        and witness.representation_revision == batch.manifest.representation_revision
        and witness.representation_binding_id == batch.manifest.representation_binding_id
        and witness.occurrence_ids == batch.occurrence_ids
    )


def _closed_batch_location(
    state: ActiveContextAdmissionState,
    batch_id: AdmissionBatchId,
) -> tuple[int, ClosedEpochAudit, AdmissionBatchRecord] | None:
    for index, audit in enumerate(state.closed_epochs):
        for record in audit.terminal_batch_records:
            if record.batch.batch_id == batch_id:
                return index, audit, record
    return None


def _closed_generation_location(
    state: ActiveContextAdmissionState,
    reservation_id: GenerationReservationId,
) -> tuple[int, ClosedEpochAudit, GenerationReservationRecord] | None:
    for index, audit in enumerate(state.closed_epochs):
        for record in audit.terminal_generation_reservations:
            if record.generation_reservation_id == reservation_id:
                return index, audit, record
    return None


def _replace_closed_audit(
    state: ActiveContextAdmissionState,
    index: int,
    audit: ClosedEpochAudit,
) -> ActiveContextAdmissionState:
    return replace(
        state,
        closed_epochs=tuple(
            audit if item_index == index else item
            for item_index, item in enumerate(state.closed_epochs)
        ),
    )


def _reconcile_deducted_closed_charge(
    state: ActiveContextAdmissionState,
    audit: ClosedEpochAudit,
    *,
    deducted_charge: int,
    terminal_charge: int,
) -> ActiveContextAdmissionState:
    if audit.fence_proof is not None or deducted_charge == terminal_charge:
        return state
    snapshot = state.snapshot
    active_count, remaining_count = _reconciled_snapshot_counts(
        snapshot.active_count,
        snapshot.remaining_count,
        snapshot.hard_limit,
        deducted_charge,
        terminal_charge,
    )
    return replace(
        state,
        snapshot=replace(snapshot, active_count=active_count, remaining_count=remaining_count),
    )


def _open_epoch(
    state: ContextAdmissionState,
    event: OpenEpochEvent,
) -> AdmissionTransition:
    if not isinstance(state, UninitializedContextAdmissionState):
        return _reject(state, event, "epoch-already-active")
    try:
        active = ActiveContextAdmissionState(
            protocol_version=state.protocol_version,
            aggregate_revision=state.aggregate_revision,
            admission_sequence=state.admission_sequence,
            snapshot=event.snapshot,
            protected_pools=event.protected_pools,
            occurrence_records=(),
            batch_records=(),
            reservations=(),
            generation_reservations=(),
            processed_events=state.processed_events,
            idempotency_records=state.idempotency_records,
            expired_idempotency_tombstones=state.expired_idempotency_tombstones,
            closed_epochs=state.closed_epochs,
        )
    except ContextAdmissionValidationError:
        return _reject(state, event, "invalid-epoch-snapshot")
    return _publish(state, active, event)


def _propose(
    state: ContextAdmissionState,
    event: ProposeOccurrenceEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    occurrence = event.occurrence
    lineage = occurrence.lineage
    is_fork_work = (
        lineage.current_session_id != lineage.root_session_id
        or lineage.current_agent_id != lineage.root_agent_id
        or lineage.current_thread_id != lineage.root_thread_id
        or lineage.parent_agent_id is not None
        or lineage.parent_thread_id is not None
        or lineage.fork_occurrence_id is not None
    )
    is_parent_delivery = (
        occurrence.producer_surface is ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY
        and lineage.delivery_occurrence_id is not None
    )
    existing = next(
        (
            record
            for record in state.occurrence_records
            if record.occurrence.occurrence_id == occurrence.occurrence_id
        ),
        None,
    )
    if existing is None:
        existing = next(
            (
                record
                for audit in state.closed_epochs
                for record in audit.terminal_occurrence_records
                if record.occurrence.occurrence_id == occurrence.occurrence_id
            ),
            None,
        )
    if existing is not None:
        if existing.occurrence == occurrence:
            return AdmissionTransition(
                next_state=state,
                decision=_decision(
                    state,
                    AdmissionDecisionKind.NOOP_IDEMPOTENT,
                    "occurrence-replay",
                ),
                effects=(),
            )
        return _reject(
            state,
            event,
            "occurrence-identity-corruption",
            kind=AdmissionDecisionKind.QUARANTINED,
        )
    if (
        occurrence.lineage.window_epoch_id != state.snapshot.window_epoch_id
        or occurrence.lineage.window_epoch_number != state.snapshot.window_epoch_number
    ):
        return _reject(state, event, "occurrence-epoch-mismatch")
    if is_fork_work and not is_parent_delivery:
        return _reject(state, event, "fork-requires-distinct-epoch")
    record = AdmissionOccurrenceRecord(
        occurrence=occurrence,
        state=AdmissionState.PROPOSED,
        batch_id=None,
        reservation_id=None,
        accepted_witness_ids=(),
        indeterminate_reason_code=None,
        quarantine_reason_code=None,
    )
    return _publish(
        state,
        replace(
            state,
            occurrence_records=tuple(
                sorted(
                    state.occurrence_records + (record,),
                    key=lambda item: item.occurrence.occurrence_id.value,
                )
            ),
        ),
        event,
    )


def _reserve(
    state: ContextAdmissionState,
    event: ReserveRequestEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    if event.snapshot_sequence != state.snapshot.snapshot_sequence:
        return _reject(state, event, "snapshot-sequence-mismatch")
    if (
        _batch_record(state, event.batch.batch_id) is not None
        or _closed_batch_location(state, event.batch.batch_id) is not None
    ):
        return _reject(state, event, "batch-already-reserved")
    reservation = event.input_reservations[0]
    if any(
        existing.reservation_id == reservation.reservation_id for existing in state.reservations
    ):
        return _reject(state, event, "reservation-id-reuse-with-changed-descriptor")
    member_records = tuple(
        record
        for record in state.occurrence_records
        if record.occurrence.occurrence_id in set(event.batch.occurrence_ids)
    )
    if (
        len(member_records) != len(event.batch.occurrence_ids)
        or tuple(record.occurrence.occurrence_id for record in member_records)
        != event.batch.occurrence_ids
        or any(record.state is not AdmissionState.PROPOSED for record in member_records)
        or any(
            record.occurrence.reserve_class is not event.batch.reserve_class
            for record in member_records
        )
    ):
        return _reject(state, event, "batch-members-not-all-proposed")
    owned_pairs = tuple(
        (span_id, member.occurrence.occurrence_id)
        for member in member_records
        for span_id in member.occurrence.owned_span_ids
    )
    owned_span_ids = tuple(span_id for span_id, _ in owned_pairs)
    manifest_pairs = tuple(
        (owner.span_id, owner.occurrence_id) for owner in event.batch.manifest.span_owners
    )
    if (
        len(owned_span_ids) != len(set(owned_span_ids))
        or set(owned_pairs) != set(manifest_pairs)
        or len(owned_pairs) != len(manifest_pairs)
    ):
        return _reject(state, event, "inconsistent-span-ownership")
    if len(event.input_reservations) != 1:
        return _reject(state, event, "atomic-input-reservation-required")
    if (
        reservation.key.batch_id != event.batch.batch_id
        or reservation.occurrence_ids != event.batch.occurrence_ids
        or reservation.snapshot_sequence != state.snapshot.snapshot_sequence
        or reservation.window_epoch_id != state.snapshot.window_epoch_id
        or reservation.window_epoch_number != state.snapshot.window_epoch_number
        or reservation.reserve_class is not event.batch.reserve_class
        or reservation.protected_pool_owner_id != event.batch.protected_pool_owner_id
    ):
        return _reject(state, event, "reservation-descriptor-mismatch")
    expected_revisions = tuple(
        (
            record.occurrence.occurrence_id,
            record.occurrence.representation_revision,
        )
        for record in member_records
    )
    if reservation.key.occurrence_revisions != expected_revisions:
        return _reject(state, event, "reservation-revision-mismatch")
    generation = event.generation_reservation
    generation_count = generation.maximum_allowance if generation is not None else 0
    if generation is not None and (
        any(
            existing.generation_reservation_id == generation.generation_reservation_id
            for existing in state.generation_reservations
        )
        or any(
            existing.generation_reservation_id == generation.generation_reservation_id
            for audit in state.closed_epochs
            for existing in audit.terminal_generation_reservations
        )
    ):
        return _reject(
            state,
            event,
            "generation-reservation-id-reuse-with-changed-descriptor",
        )
    if generation is not None and (
        generation.request_id != event.batch.request_id
        or generation.batch_id != event.batch.batch_id
        or generation.representation_revision != event.batch.manifest.representation_revision
        or generation.occurrence_ids != event.batch.occurrence_ids
        or generation.window_epoch_id != state.snapshot.window_epoch_id
        or generation.window_epoch_number != state.snapshot.window_epoch_number
        or generation.snapshot_sequence != state.snapshot.snapshot_sequence
        or generation.reserve_class is not event.batch.reserve_class
        or generation.protected_pool_owner_id != event.batch.protected_pool_owner_id
    ):
        return _reject(state, event, "generation-descriptor-mismatch")
    requested = reservation.reserved_count + generation_count
    global_available, ordinary_available, pool_available = _capacity(state)
    if event.batch.protected_pool_owner_id is None:
        available = ordinary_available
    else:
        available = min(
            global_available,
            pool_available.get(
                (
                    event.batch.reserve_class,
                    event.batch.protected_pool_owner_id,
                ),
                0,
            ),
        )
    if requested > available:
        return _reject(
            state,
            event,
            "insufficient-capacity",
            requested_count=requested,
            reserve_class=event.batch.reserve_class,
            protected_pool_owner_id=event.batch.protected_pool_owner_id,
        )
    if event.batch.protected_pool_owner_id is not None:
        pool = next(
            (
                item
                for item in state.protected_pools
                if item.reserve_class is event.batch.reserve_class
                and item.capability_owner_id == event.batch.protected_pool_owner_id
            ),
            None,
        )
        if pool is None:
            return _reject(state, event, "unknown-protected-pool")
    batch_record = AdmissionBatchRecord(
        batch=event.batch,
        state=AdmissionState.RESERVED,
        reservation_id=reservation.reservation_id,
        witness_ids=(),
        committed_input_count=0,
        unresolved_input_count=0,
    )
    member_ids = set(event.batch.occurrence_ids)
    reserved_occurrence_records = tuple(
        replace(
            record,
            state=AdmissionState.RESERVED,
            batch_id=event.batch.batch_id,
            reservation_id=reservation.reservation_id,
        )
        if record.occurrence.occurrence_id in member_ids
        else record
        for record in state.occurrence_records
    )
    next_state = replace(
        state,
        occurrence_records=reserved_occurrence_records,
        batch_records=tuple(
            sorted(
                state.batch_records + (batch_record,),
                key=lambda item: item.batch.batch_id.value,
            )
        ),
        reservations=tuple(
            sorted(
                state.reservations + event.input_reservations,
                key=lambda item: item.reservation_id.value,
            )
        ),
        generation_reservations=tuple(
            sorted(
                state.generation_reservations
                + ((generation,) if generation is not None and generation_count > 0 else ()),
                key=lambda item: item.generation_reservation_id.value,
            )
        ),
    )
    reserve_decision = _decision(
        next_state,
        AdmissionDecisionKind.WOULD_ADMIT,
        "accepted",
        requested_count=requested,
        reserve_class=event.batch.reserve_class,
        protected_pool_owner_id=event.batch.protected_pool_owner_id,
    )
    idempotency_record = IdempotencyRecord(
        namespace=event.idempotency_namespace,
        reservation_key=reservation.key,
        original_descriptor=event,
        original_reserve_decision=reserve_decision,
        owning_event_id=event.event_id,
        publication_revision=type(state.aggregate_revision)(state.aggregate_revision.value + 1),
    )
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
    effects: tuple[AdmissionEffect, ...] = (
        ReservationRecordedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=reservation.reservation_id,
            charge_domain=ChargeDomain.INPUT_CONTEXT,
            reserve_class=reservation.reserve_class,
            protected_pool_owner_id=reservation.protected_pool_owner_id,
            count=reservation.reserved_count,
            window_epoch_id=reservation.window_epoch_id,
            snapshot_sequence=reservation.snapshot_sequence,
            witness_ids=(),
        ),
        *_occurrence_effects(
            state,
            event,
            event.batch,
            AdmissionState.PROPOSED,
            AdmissionState.RESERVED,
            capacity_changed=True,
        ),
    )
    if generation is not None and generation_count > 0:
        effects += (
            GenerationReservationRecordedEffect(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=generation.generation_reservation_id,
                charge_domain=ChargeDomain.OUTPUT_GENERATION,
                reserve_class=generation.reserve_class,
                protected_pool_owner_id=generation.protected_pool_owner_id,
                count=generation_count,
                window_epoch_id=generation.window_epoch_id,
                snapshot_sequence=generation.snapshot_sequence,
                witness_ids=(),
            ),
        )
    return _publish(
        state,
        next_state,
        event,
        requested_count=requested,
        reserve_class=event.batch.reserve_class,
        protected_pool_owner_id=event.batch.protected_pool_owner_id,
        capacity_changed=True,
        effects=effects,
        idempotency_record=idempotency_record,
    )


def _prepare(
    state: ContextAdmissionState,
    event: PrepareBatchEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None or record.state is not AdmissionState.RESERVED:
        return _reject(state, event, "illegal-prepare-order")
    if event.representation_revision != record.batch.manifest.representation_revision:
        return _reject(state, event, "representation-revision-mismatch")
    if event.representation_binding_id != record.batch.manifest.representation_binding_id:
        return _reject(state, event, "representation-binding-mismatch")
    reservation = _reservation_for(state, record)
    if reservation is None or event.proposed_charge != reservation.reserved_count:
        return _reject(state, event, "prepared-charge-mismatch")
    if event.measurement_kind not in {
        MeasurementKind.PROVIDER_EXACT,
        MeasurementKind.TOKENIZER_EXACT,
    }:
        return _reject(state, event, "non-authoritative-measurement")
    updated = replace(
        record,
        state=AdmissionState.PREPARED,
    )
    next_state = _replace_batch_record(state, updated)
    next_state = _set_occurrence_state(
        next_state,
        record.batch,
        AdmissionState.PREPARED,
    )
    return _publish(
        state,
        next_state,
        event,
        effects=_occurrence_effects(
            state,
            event,
            record.batch,
            AdmissionState.RESERVED,
            AdmissionState.PREPARED,
            capacity_changed=False,
        ),
    )


def _stage(
    state: ContextAdmissionState,
    event: StageHistoryEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    record = _batch_record(state, event.batch_id)
    if (
        record is None
        or record.state is not AdmissionState.PREPARED
        or not _validate_witness(
            state,
            record.batch,
            event.witness,
            WitnessKind.HISTORY_STAGED,
        )
    ):
        return _reject(state, event, "invalid-history-stage-witness")
    updated = replace(
        record,
        state=AdmissionState.HISTORY_STAGED,
        witness_ids=_append_witness_ids(
            record.witness_ids,
            event.witness.witness_id,
        ),
    )
    next_state = _replace_batch_record(state, updated)
    next_state = _set_occurrence_state(
        next_state,
        record.batch,
        AdmissionState.HISTORY_STAGED,
        witness=event.witness,
    )
    return _publish(
        state,
        next_state,
        event,
        effects=_occurrence_effects(
            state,
            event,
            record.batch,
            AdmissionState.PREPARED,
            AdmissionState.HISTORY_STAGED,
            capacity_changed=False,
        ),
    )


def _dispatch(
    state: ContextAdmissionState,
    event: DispatchRequestEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    record = _batch_record(state, event.batch_id)
    if (
        record is None
        or record.state is not AdmissionState.HISTORY_STAGED
        or not _validate_witness(
            state,
            record.batch,
            event.witness,
            WitnessKind.REQUEST_INCLUDED,
        )
    ):
        return _reject(state, event, "invalid-request-inclusion-witness")
    updated = replace(
        record,
        state=AdmissionState.REQUEST_DISPATCHED,
        witness_ids=_append_witness_ids(
            record.witness_ids,
            event.witness.witness_id,
        ),
    )
    next_state = _replace_batch_record(state, updated)
    next_state = _set_occurrence_state(
        next_state,
        record.batch,
        AdmissionState.REQUEST_DISPATCHED,
        witness=event.witness,
    )
    return _publish(
        state,
        next_state,
        event,
        effects=_occurrence_effects(
            state,
            event,
            record.batch,
            record.state,
            AdmissionState.REQUEST_DISPATCHED,
            capacity_changed=False,
        ),
    )


def _accepted_state(
    state: ActiveContextAdmissionState,
    record: AdmissionBatchRecord,
    witness: AdmissionWitness,
    exact_charge: int,
) -> tuple[ActiveContextAdmissionState, AdmissionDecisionKind, str]:
    reservation = _reservation_for(state, record)
    if reservation is None:
        return state, AdmissionDecisionKind.QUARANTINED, "missing-reservation"
    quarantined = (
        exact_charge > reservation.reserved_count or exact_charge > state.snapshot.hard_limit
    )
    lifecycle = AdmissionState.QUARANTINED if quarantined else AdmissionState.COMMITTED
    updated = replace(
        record,
        state=lifecycle,
        witness_ids=_append_witness_ids(record.witness_ids, witness.witness_id),
        committed_input_count=exact_charge,
        unresolved_input_count=0,
    )
    next_state = _replace_batch_record(state, updated)
    next_state = _set_occurrence_state(
        next_state,
        record.batch,
        lifecycle,
        witness=witness,
        quarantine_reason_code=("provider-charge-exceeds-reservation" if quarantined else None),
    )
    return (
        next_state,
        (AdmissionDecisionKind.QUARANTINED if quarantined else AdmissionDecisionKind.WOULD_ADMIT),
        "provider-charge-exceeds-reservation" if quarantined else "accepted",
    )


def _accept_closed_input(
    state: ActiveContextAdmissionState,
    event: AcceptInputEvent | ResolveIndeterminateAcceptedEvent,
    location: tuple[int, ClosedEpochAudit, AdmissionBatchRecord],
) -> AdmissionTransition:
    index, audit, record = location
    expected_state = (
        AdmissionState.REQUEST_DISPATCHED
        if isinstance(event, AcceptInputEvent)
        else AdmissionState.INDETERMINATE
    )
    binding = event.representation_binding_witness
    expected_revision = record.batch.manifest.representation_revision
    exact_charge = (
        event.exact_input_charge if isinstance(event, AcceptInputEvent) else event.exact_charge
    )
    if (
        record.state is not expected_state
        or not _validate_witness_for_snapshot(
            audit.snapshot,
            record.batch,
            event.witness,
            WitnessKind.PROVIDER_ACCEPTED,
        )
        or event.measurement_kind is not MeasurementKind.PROVIDER_EXACT
        or event.final_manifest_revision != expected_revision
        or event.final_manifest != record.batch.manifest
        or event.final_manifest.representation_revision != expected_revision
        or event.final_manifest.request_id != record.batch.request_id
        or binding.counted_representation_revision != expected_revision
        or binding.dispatched_representation_revision != expected_revision
        or binding.final_manifest_revision != expected_revision
        or binding.representation_binding_id != record.batch.manifest.representation_binding_id
        or binding.request_id != record.batch.request_id
        or binding.batch_id != record.batch.batch_id
    ):
        return _reject(state, event, "invalid-closed-epoch-acceptance")
    reservation = audit.reservation_for(record)
    if reservation is None:
        return _reject(state, event, "missing-closed-epoch-reservation")
    member_ids = set(record.batch.occurrence_ids)
    expected_owned_pairs = tuple(
        (span_id, item.occurrence.occurrence_id)
        for item in audit.terminal_occurrence_records
        if item.occurrence.occurrence_id in member_ids
        for span_id in item.occurrence.owned_span_ids
    )
    manifest_pairs = tuple(
        (owner.span_id, owner.occurrence_id) for owner in event.final_manifest.span_owners
    )
    manifest_invalid = (
        len({span_id for span_id, _ in expected_owned_pairs}) != len(expected_owned_pairs)
        or set(manifest_pairs) != set(expected_owned_pairs)
        or len(manifest_pairs) != len(expected_owned_pairs)
    )
    authority_mismatch = (
        event.authority_source != event.witness.authority_source_id
        or event.authority_source != binding.authority_source_id
    )
    quarantined = (
        authority_mismatch
        or exact_charge > reservation.reserved_count
        or exact_charge > audit.snapshot.hard_limit
        or manifest_invalid
    )
    reason_code = (
        "authority-source-mismatch"
        if authority_mismatch
        else "incomplete-canonical-span-ownership"
        if manifest_invalid
        else "provider-charge-exceeds-reservation"
        if quarantined
        else "accepted"
    )
    lifecycle = AdmissionState.QUARANTINED if quarantined else AdmissionState.COMMITTED
    updated_record = replace(
        record,
        state=lifecycle,
        witness_ids=_append_witness_ids(
            record.witness_ids,
            event.witness.witness_id,
        ),
        committed_input_count=exact_charge,
        unresolved_input_count=0,
    )
    batch_records = tuple(
        updated_record if item.batch.batch_id == record.batch.batch_id else item
        for item in audit.terminal_batch_records
    )
    occurrence_records = tuple(
        replace(
            item,
            state=lifecycle,
            accepted_witness_ids=_append_witness_ids(
                item.accepted_witness_ids,
                event.witness.witness_id,
            ),
            quarantine_reason_code=(reason_code if quarantined else None),
        )
        if item.occurrence.occurrence_id in member_ids
        else item
        for item in audit.terminal_occurrence_records
    )
    updated_audit = replace(
        audit,
        terminal_occurrence_records=occurrence_records,
        terminal_batch_records=batch_records,
        retained_unresolved_count=audit.retained_input_count(batch_records),
    )
    next_state = _replace_closed_audit(state, index, updated_audit)
    next_state = _reconcile_deducted_closed_charge(
        next_state,
        audit,
        deducted_charge=audit.retained_input_count((record,)),
        terminal_charge=exact_charge,
    )
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
    effects: tuple[AdmissionEffect, ...] = (
        ChargeCommittedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=record.batch.batch_id,
            charge_domain=ChargeDomain.INPUT_CONTEXT,
            reserve_class=record.batch.reserve_class,
            protected_pool_owner_id=record.batch.protected_pool_owner_id,
            count=exact_charge,
            window_epoch_id=audit.snapshot.window_epoch_id,
            snapshot_sequence=audit.snapshot.snapshot_sequence,
            witness_ids=(event.witness.witness_id,),
        ),
        *_occurrence_effects(
            state,
            event,
            record.batch,
            record.state,
            lifecycle,
            capacity_changed=True,
        ),
    )
    if quarantined:
        effects += (
            QuarantineRecordedEffect(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=record.batch.batch_id,
                reason_code=reason_code,
            ),
        )
    return _publish(
        state,
        next_state,
        event,
        kind=(
            AdmissionDecisionKind.QUARANTINED if quarantined else AdmissionDecisionKind.WOULD_ADMIT
        ),
        reason_code=reason_code,
        requested_count=exact_charge,
        reserve_class=record.batch.reserve_class,
        protected_pool_owner_id=record.batch.protected_pool_owner_id,
        capacity_changed=True,
        effects=effects,
    )


def _accept(
    state: ContextAdmissionState,
    event: AcceptInputEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None:
        location = _closed_batch_location(state, event.batch_id)
        if location is not None:
            return _accept_closed_input(state, event, location)
    if record is None or record.state is not AdmissionState.REQUEST_DISPATCHED:
        return _reject(state, event, "illegal-accept-order")
    if not _validate_witness(
        state,
        record.batch,
        event.witness,
        WitnessKind.PROVIDER_ACCEPTED,
    ):
        return _reject(state, event, "invalid-provider-acceptance-witness")
    binding = event.representation_binding_witness
    expected_revision = record.batch.manifest.representation_revision
    if (
        event.final_manifest != record.batch.manifest
        or binding.representation_binding_id != record.batch.manifest.representation_binding_id
    ):
        return _reject(state, event, "representation-binding-mismatch")
    if (
        event.final_manifest_revision != expected_revision
        or event.final_manifest.representation_revision != expected_revision
        or event.final_manifest.request_id != record.batch.request_id
        or binding.counted_representation_revision != expected_revision
        or binding.dispatched_representation_revision != expected_revision
        or binding.final_manifest_revision != expected_revision
        or binding.request_id != record.batch.request_id
        or binding.batch_id != record.batch.batch_id
    ):
        return _reject(state, event, "representation-revision-mismatch")
    if event.measurement_kind is not MeasurementKind.PROVIDER_EXACT:
        return _reject(state, event, "non-authoritative-measurement")
    if event.exact_input_charge < 0:
        return _reject(state, event, "invalid-exact-charge")
    expected_owned_spans: list[CanonicalSpanId] = []
    expected_owned_pairs: list[tuple[CanonicalSpanId, AdmissionOccurrenceId]] = []
    for occurrence in state.occurrence_records:
        if occurrence.occurrence.occurrence_id in set(record.batch.occurrence_ids):
            expected_owned_spans.extend(occurrence.occurrence.owned_span_ids)
            expected_owned_pairs.extend(
                (span_id, occurrence.occurrence.occurrence_id)
                for span_id in occurrence.occurrence.owned_span_ids
            )
    manifest_pairs = tuple(
        (owner.span_id, owner.occurrence_id) for owner in event.final_manifest.span_owners
    )
    if (
        event.authority_source != event.witness.authority_source_id
        or event.authority_source != binding.authority_source_id
    ):
        reason_code = "authority-source-mismatch"
        next_state = _quarantined_acceptance_state(
            state,
            record,
            event.witness,
            event.exact_input_charge,
            reason_code,
        )
        return _publish(
            state,
            next_state,
            event,
            kind=AdmissionDecisionKind.QUARANTINED,
            reason_code=reason_code,
            requested_count=event.exact_input_charge,
            reserve_class=record.batch.reserve_class,
            protected_pool_owner_id=record.batch.protected_pool_owner_id,
            capacity_changed=True,
            effects=_acceptance_effects(
                state,
                event,
                record,
                event.exact_input_charge,
                event.witness,
                quarantine_reason_code=reason_code,
            ),
        )
    if (
        len(expected_owned_spans) != len(set(expected_owned_spans))
        or set(manifest_pairs) != set(expected_owned_pairs)
        or len(manifest_pairs) != len(expected_owned_pairs)
    ):
        reason_code = "incomplete-canonical-span-ownership"
        next_state = _quarantined_acceptance_state(
            state,
            record,
            event.witness,
            event.exact_input_charge,
            reason_code,
        )
        return _publish(
            state,
            next_state,
            event,
            kind=AdmissionDecisionKind.QUARANTINED,
            reason_code=reason_code,
            requested_count=event.exact_input_charge,
            reserve_class=record.batch.reserve_class,
            protected_pool_owner_id=record.batch.protected_pool_owner_id,
            capacity_changed=True,
            effects=_acceptance_effects(
                state,
                event,
                record,
                event.exact_input_charge,
                event.witness,
                quarantine_reason_code=reason_code,
            ),
        )
    next_state, kind, reason = _accepted_state(
        state,
        record,
        event.witness,
        event.exact_input_charge,
    )
    return _publish(
        state,
        next_state,
        event,
        kind=kind,
        reason_code=reason,
        requested_count=event.exact_input_charge,
        reserve_class=record.batch.reserve_class,
        protected_pool_owner_id=record.batch.protected_pool_owner_id,
        capacity_changed=True,
        effects=_accepted_effects(
            state,
            event,
            record,
            event.exact_input_charge,
            event.witness,
        ),
    )


def _release_closed_batch(
    state: ActiveContextAdmissionState,
    event: (
        ReleaseNonAdmissionEvent
        | RollbackAdmissionEvent
        | ResolveIndeterminateNonAdmissionEvent
        | ResolveIndeterminateRollbackEvent
    ),
    location: tuple[int, ClosedEpochAudit, AdmissionBatchRecord],
) -> AdmissionTransition:
    index, audit, record = location
    released_input_count = audit.retained_input_count((record,))
    is_release = isinstance(
        event,
        ReleaseNonAdmissionEvent | ResolveIndeterminateNonAdmissionEvent,
    )
    is_resolution = isinstance(
        event,
        ResolveIndeterminateNonAdmissionEvent | ResolveIndeterminateRollbackEvent,
    )
    expected_state = (
        AdmissionState.INDETERMINATE if is_resolution else AdmissionState.REQUEST_DISPATCHED
    )
    expected_kind = WitnessKind.NON_ADMISSION if is_release else WitnessKind.ROLLBACK
    required_release_kind = _required_release_witness_kind(
        state,
        record.batch,
        snapshot=audit.snapshot,
    )
    if (
        record.state is not expected_state
        or (
            record.batch.protected_pool_owner_id is not None
            and required_release_kind is not expected_kind
        )
        or not _validate_witness_for_snapshot(
            audit.snapshot,
            record.batch,
            event.witness,
            expected_kind,
        )
    ):
        return _reject(state, event, "invalid-closed-epoch-resolution")
    lifecycle = AdmissionState.RELEASED if is_release else AdmissionState.ROLLED_BACK
    witness_ids = _append_witness_ids(
        record.witness_ids,
        event.witness.witness_id,
    )
    updated_record = replace(
        record,
        state=lifecycle,
        witness_ids=witness_ids,
        unresolved_input_count=0,
    )
    batch_records = tuple(
        updated_record if item.batch.batch_id == record.batch.batch_id else item
        for item in audit.terminal_batch_records
    )
    member_ids = set(record.batch.occurrence_ids)
    occurrence_records = tuple(
        replace(
            item,
            state=lifecycle,
            accepted_witness_ids=_append_witness_ids(
                item.accepted_witness_ids,
                event.witness.witness_id,
            ),
            indeterminate_reason_code=None,
        )
        if item.occurrence.occurrence_id in member_ids
        else item
        for item in audit.terminal_occurrence_records
    )
    updated_audit = replace(
        audit,
        terminal_occurrence_records=occurrence_records,
        terminal_batch_records=batch_records,
        retained_unresolved_count=audit.retained_input_count(batch_records),
    )
    next_state = _replace_closed_audit(state, index, updated_audit)
    reservation = audit.reservation_for(record)
    effects: tuple[AdmissionEffect, ...] = _occurrence_effects(
        state,
        event,
        record.batch,
        record.state,
        lifecycle,
        capacity_changed=True,
    )
    if reservation is not None:
        revision, sequence = _effect_coordinates(state, capacity_changed=True)
        effects = (
            ReservationReleasedEffect(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=reservation.reservation_id,
                charge_domain=ChargeDomain.INPUT_CONTEXT,
                reserve_class=reservation.reserve_class,
                protected_pool_owner_id=reservation.protected_pool_owner_id,
                count=reservation.reserved_count,
                window_epoch_id=reservation.window_epoch_id,
                snapshot_sequence=reservation.snapshot_sequence,
                witness_ids=(event.witness.witness_id,),
            ),
            *effects,
        )
    generation_effects: tuple[AdmissionEffect, ...] = ()
    generation_records: list[GenerationReservationRecord] = []
    invalidated_generation_count = 0
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
    for generation in audit.terminal_generation_reservations:
        if generation.batch_id == record.batch.batch_id and generation.state in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        }:
            generation_effects += (
                ReservationInvalidatedEffect(
                    source_event_id=event.event_id,
                    resulting_aggregate_revision=revision,
                    resulting_admission_sequence=sequence,
                    target_id=generation.generation_reservation_id,
                    charge_domain=ChargeDomain.OUTPUT_GENERATION,
                    reserve_class=generation.reserve_class,
                    protected_pool_owner_id=generation.protected_pool_owner_id,
                    count=generation.maximum_allowance,
                    window_epoch_id=generation.window_epoch_id,
                    snapshot_sequence=generation.snapshot_sequence,
                    witness_ids=(event.witness.witness_id,),
                ),
            )
            invalidated_generation_count += generation.maximum_allowance
        else:
            generation_records.append(generation)
    if generation_effects:
        updated_audit = replace(
            updated_audit,
            terminal_generation_reservations=tuple(generation_records),
            retained_generation_count=sum(
                generation.maximum_allowance
                for generation in generation_records
                if generation.state
                in {
                    GenerationState.RESERVED,
                    GenerationState.STREAMING,
                    GenerationState.INDETERMINATE,
                }
            ),
        )
        next_state = _replace_closed_audit(state, index, updated_audit)
        effects += generation_effects
    next_state = _reconcile_deducted_closed_charge(
        next_state,
        audit,
        deducted_charge=released_input_count + invalidated_generation_count,
        terminal_charge=0,
    )
    return _publish(
        state,
        next_state,
        event,
        capacity_changed=True,
        effects=effects,
    )


def _release_or_rollback(
    state: ContextAdmissionState,
    event: (
        ReleaseNonAdmissionEvent
        | RollbackAdmissionEvent
        | ResolveIndeterminateNonAdmissionEvent
        | ResolveIndeterminateRollbackEvent
    ),
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None:
        location = _closed_batch_location(state, event.batch_id)
        if location is not None:
            return _release_closed_batch(state, event, location)
        return _reject(state, event, "unknown-batch")
    is_release = isinstance(
        event,
        ReleaseNonAdmissionEvent | ResolveIndeterminateNonAdmissionEvent,
    )
    is_resolution = isinstance(
        event,
        ResolveIndeterminateNonAdmissionEvent | ResolveIndeterminateRollbackEvent,
    )
    if is_resolution:
        allowed_states = {AdmissionState.INDETERMINATE}
    elif is_release:
        allowed_states = {
            AdmissionState.RESERVED,
            AdmissionState.PREPARED,
            AdmissionState.HISTORY_STAGED,
            AdmissionState.REQUEST_DISPATCHED,
        }
    else:
        allowed_states = {
            AdmissionState.HISTORY_STAGED,
            AdmissionState.REQUEST_DISPATCHED,
        }
    expected_witness = WitnessKind.NON_ADMISSION if is_release else WitnessKind.ROLLBACK
    required_release_kind = _required_release_witness_kind(state, record.batch)
    if (
        record.batch.protected_pool_owner_id is not None
        and required_release_kind is not expected_witness
    ):
        return _reject(state, event, "protected-release-policy-mismatch")
    if record.state not in allowed_states or not _validate_witness(
        state,
        record.batch,
        event.witness,
        expected_witness,
    ):
        return _reject(state, event, "invalid-release-or-rollback-witness")
    lifecycle = AdmissionState.RELEASED if is_release else AdmissionState.ROLLED_BACK
    updated = replace(
        record,
        state=lifecycle,
        witness_ids=_append_witness_ids(
            record.witness_ids,
            event.witness.witness_id,
        ),
        unresolved_input_count=0,
    )
    next_state = _replace_batch_record(state, updated)
    next_state = _set_occurrence_state(
        next_state,
        record.batch,
        lifecycle,
        witness=event.witness,
    )
    effects: tuple[AdmissionEffect, ...] = _occurrence_effects(
        state,
        event,
        record.batch,
        record.state,
        lifecycle,
        capacity_changed=True,
    )
    reservation = _reservation_for(state, record)
    if reservation is not None:
        revision, sequence = _effect_coordinates(state, capacity_changed=True)
        effects = (
            ReservationReleasedEffect(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=reservation.reservation_id,
                charge_domain=ChargeDomain.INPUT_CONTEXT,
                reserve_class=reservation.reserve_class,
                protected_pool_owner_id=reservation.protected_pool_owner_id,
                count=reservation.reserved_count,
                window_epoch_id=reservation.window_epoch_id,
                snapshot_sequence=reservation.snapshot_sequence,
                witness_ids=(event.witness.witness_id,),
            ),
            *effects,
        )
    generation_effects: tuple[AdmissionEffect, ...] = ()
    generation_records: list[GenerationReservationRecord] = []
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
    for generation in next_state.generation_reservations:
        if generation.batch_id == record.batch.batch_id and generation.state in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        }:
            generation_effects += (
                ReservationInvalidatedEffect(
                    source_event_id=event.event_id,
                    resulting_aggregate_revision=revision,
                    resulting_admission_sequence=sequence,
                    target_id=generation.generation_reservation_id,
                    charge_domain=ChargeDomain.OUTPUT_GENERATION,
                    reserve_class=generation.reserve_class,
                    protected_pool_owner_id=generation.protected_pool_owner_id,
                    count=generation.maximum_allowance,
                    window_epoch_id=generation.window_epoch_id,
                    snapshot_sequence=generation.snapshot_sequence,
                    witness_ids=(event.witness.witness_id,),
                ),
            )
        else:
            generation_records.append(generation)
    if generation_effects:
        next_state = replace(
            next_state,
            generation_reservations=tuple(generation_records),
        )
        effects += generation_effects
    return _publish(
        state,
        next_state,
        event,
        capacity_changed=True,
        effects=effects,
    )


def _mark_indeterminate(
    state: ContextAdmissionState,
    event: MarkIndeterminateEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None or record.state not in {
        AdmissionState.PREPARED,
        AdmissionState.HISTORY_STAGED,
        AdmissionState.REQUEST_DISPATCHED,
    }:
        return _reject(state, event, "illegal-indeterminate-order")
    reservation = _reservation_for(state, record)
    unresolved = reservation.reserved_count if reservation is not None else 0
    updated = replace(
        record,
        state=AdmissionState.INDETERMINATE,
        unresolved_input_count=unresolved,
    )
    next_state = _replace_batch_record(state, updated)
    next_state = _set_occurrence_state(
        next_state,
        record.batch,
        AdmissionState.INDETERMINATE,
        indeterminate_reason_code=event.reason_code,
    )
    return _publish(
        state,
        next_state,
        event,
        effects=_occurrence_effects(
            state,
            event,
            record.batch,
            record.state,
            AdmissionState.INDETERMINATE,
            capacity_changed=False,
        ),
    )


def _resolve_indeterminate_accepted(
    state: ContextAdmissionState,
    event: ResolveIndeterminateAcceptedEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None:
        location = _closed_batch_location(state, event.batch_id)
        if location is not None:
            return _accept_closed_input(state, event, location)
    binding = event.representation_binding_witness
    expected_revision = (
        record.batch.manifest.representation_revision if record is not None else None
    )
    if (
        record is None
        or record.state is not AdmissionState.INDETERMINATE
        or not _validate_witness(
            state,
            record.batch,
            event.witness,
            WitnessKind.PROVIDER_ACCEPTED,
        )
        or event.authority_source != event.witness.authority_source_id
        or event.authority_source != binding.authority_source_id
        or event.measurement_kind is not MeasurementKind.PROVIDER_EXACT
        or event.final_manifest_revision != expected_revision
        or event.final_manifest != record.batch.manifest
        or event.final_manifest.representation_revision != expected_revision
        or event.final_manifest.request_id != record.batch.request_id
        or binding.counted_representation_revision != expected_revision
        or binding.dispatched_representation_revision != expected_revision
        or binding.final_manifest_revision != expected_revision
        or binding.representation_binding_id != record.batch.manifest.representation_binding_id
        or binding.request_id != record.batch.request_id
        or binding.batch_id != record.batch.batch_id
    ):
        return _reject(state, event, "invalid-indeterminate-acceptance")
    member_ids = set(record.batch.occurrence_ids)
    expected_owned_pairs = tuple(
        (span_id, item.occurrence.occurrence_id)
        for item in state.occurrence_records
        if item.occurrence.occurrence_id in member_ids
        for span_id in item.occurrence.owned_span_ids
    )
    manifest_pairs = tuple(
        (owner.span_id, owner.occurrence_id) for owner in event.final_manifest.span_owners
    )
    if (
        len({span_id for span_id, _ in expected_owned_pairs}) != len(expected_owned_pairs)
        or set(manifest_pairs) != set(expected_owned_pairs)
        or len(manifest_pairs) != len(expected_owned_pairs)
    ):
        reason_code = "incomplete-canonical-span-ownership"
        next_state = _quarantined_acceptance_state(
            state,
            record,
            event.witness,
            event.exact_charge,
            reason_code,
        )
        return _publish(
            state,
            next_state,
            event,
            kind=AdmissionDecisionKind.QUARANTINED,
            reason_code=reason_code,
            requested_count=event.exact_charge,
            reserve_class=record.batch.reserve_class,
            protected_pool_owner_id=record.batch.protected_pool_owner_id,
            capacity_changed=True,
            effects=_acceptance_effects(
                state,
                event,
                record,
                event.exact_charge,
                event.witness,
                quarantine_reason_code=reason_code,
            ),
        )
    next_state, kind, reason = _accepted_state(
        state,
        record,
        event.witness,
        event.exact_charge,
    )
    return _publish(
        state,
        next_state,
        event,
        kind=kind,
        reason_code=reason,
        requested_count=event.exact_charge,
        reserve_class=record.batch.reserve_class,
        protected_pool_owner_id=record.batch.protected_pool_owner_id,
        capacity_changed=True,
        effects=_accepted_effects(
            state,
            event,
            record,
            event.exact_charge,
            event.witness,
        ),
    )


def _start_generation(
    state: ContextAdmissionState,
    event: StartGenerationEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    generation = _generation_record(state, event.generation_reservation_id)
    if generation is None or generation.state is not GenerationState.RESERVED:
        return _reject(state, event, "illegal-generation-start")
    batch_record = _batch_record(state, generation.batch_id)
    batch = batch_record.batch if batch_record is not None else None
    if (
        batch is None
        or batch_record is None
        or batch_record.state is not AdmissionState.REQUEST_DISPATCHED
        or not _validate_witness(
            state,
            batch,
            event.witness,
            WitnessKind.REQUEST_INCLUDED,
        )
        or (
            generation.authority_source_id is not None
            and generation.authority_source_id != event.witness.authority_source_id
        )
    ):
        return _reject(state, event, "invalid-generation-start-witness")
    updated = replace(
        generation,
        state=GenerationState.STREAMING,
        authority_source_id=event.witness.authority_source_id,
        witness_ids=_append_witness_ids(
            generation.witness_ids,
            event.witness.witness_id,
        ),
    )
    next_state = replace(
        state,
        generation_reservations=tuple(
            updated
            if record.generation_reservation_id == updated.generation_reservation_id
            else record
            for record in state.generation_reservations
        ),
    )
    return _publish(state, next_state, event)


def _reconcile_closed_generation(
    state: ActiveContextAdmissionState,
    event: ReconcileGenerationEvent,
    location: tuple[int, ClosedEpochAudit, GenerationReservationRecord],
) -> AdmissionTransition:
    index, audit, generation = location
    batch_record = next(
        (
            record
            for record in audit.terminal_batch_records
            if record.batch.batch_id == generation.batch_id
        ),
        None,
    )
    if (
        generation.state
        not in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        }
        or batch_record is None
        or not _validate_witness_for_snapshot(
            audit.snapshot,
            batch_record.batch,
            event.output_usage_witness,
            WitnessKind.OUTPUT_USAGE,
        )
        or (
            generation.authority_source_id is not None
            and generation.authority_source_id != event.output_usage_witness.authority_source_id
        )
    ):
        return _reject(state, event, "invalid-closed-generation-witness")
    quarantined = event.exact_output_usage > generation.maximum_allowance
    updated = replace(
        generation,
        state=(GenerationState.QUARANTINED if quarantined else GenerationState.RECONCILED),
        exact_terminal_usage=event.exact_output_usage,
        witness_ids=_append_witness_ids(
            generation.witness_ids,
            event.output_usage_witness.witness_id,
        ),
        authority_source_id=event.output_usage_witness.authority_source_id,
    )
    generation_records = tuple(
        updated if item.generation_reservation_id == generation.generation_reservation_id else item
        for item in audit.terminal_generation_reservations
    )
    retained_generation_count = sum(
        item.maximum_allowance
        for item in generation_records
        if item.state
        in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        }
    )
    updated_audit = replace(
        audit,
        terminal_generation_reservations=generation_records,
        retained_generation_count=retained_generation_count,
    )
    next_state = _replace_closed_audit(state, index, updated_audit)
    next_state = _reconcile_deducted_closed_charge(
        next_state,
        audit,
        deducted_charge=generation.maximum_allowance,
        terminal_charge=event.exact_output_usage,
    )
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
    effects: tuple[AdmissionEffect, ...] = (
        GenerationReconciledEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=generation.generation_reservation_id,
            charge_domain=ChargeDomain.OUTPUT_GENERATION,
            reserve_class=generation.reserve_class,
            protected_pool_owner_id=generation.protected_pool_owner_id,
            count=event.exact_output_usage,
            window_epoch_id=audit.snapshot.window_epoch_id,
            snapshot_sequence=audit.snapshot.snapshot_sequence,
            witness_ids=(event.output_usage_witness.witness_id,),
        ),
    )
    reason_code = "generation-usage-exceeds-allowance" if quarantined else "accepted"
    if quarantined:
        effects += (
            QuarantineRecordedEffect(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=generation.generation_reservation_id,
                reason_code=reason_code,
            ),
        )
    return _publish(
        state,
        next_state,
        event,
        kind=(
            AdmissionDecisionKind.QUARANTINED if quarantined else AdmissionDecisionKind.WOULD_ADMIT
        ),
        reason_code=reason_code,
        capacity_changed=True,
        effects=effects,
    )


def _reconcile_generation(
    state: ContextAdmissionState,
    event: ReconcileGenerationEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    generation = _generation_record(state, event.generation_reservation_id)
    if generation is None:
        location = _closed_generation_location(
            state,
            event.generation_reservation_id,
        )
        if location is not None:
            return _reconcile_closed_generation(state, event, location)
    if generation is None or generation.state not in {
        GenerationState.STREAMING,
        GenerationState.INDETERMINATE,
    }:
        return _reject(state, event, "illegal-generation-reconciliation")
    batch_record = _batch_record(state, generation.batch_id)
    if (
        batch_record is None
        or not _validate_witness(
            state,
            batch_record.batch,
            event.output_usage_witness,
            WitnessKind.OUTPUT_USAGE,
        )
        or (
            generation.authority_source_id is not None
            and generation.authority_source_id != event.output_usage_witness.authority_source_id
        )
    ):
        return _reject(state, event, "invalid-output-usage-witness")
    quarantined = event.exact_output_usage > generation.maximum_allowance
    updated = replace(
        generation,
        state=(GenerationState.QUARANTINED if quarantined else GenerationState.RECONCILED),
        exact_terminal_usage=event.exact_output_usage,
        authority_source_id=event.output_usage_witness.authority_source_id,
        witness_ids=_append_witness_ids(
            generation.witness_ids,
            event.output_usage_witness.witness_id,
        ),
    )
    next_state = replace(
        state,
        generation_reservations=tuple(
            updated
            if record.generation_reservation_id == updated.generation_reservation_id
            else record
            for record in state.generation_reservations
        ),
    )
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
    effects: tuple[AdmissionEffect, ...] = (
        GenerationReconciledEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=generation.generation_reservation_id,
            charge_domain=ChargeDomain.OUTPUT_GENERATION,
            reserve_class=generation.reserve_class,
            protected_pool_owner_id=generation.protected_pool_owner_id,
            count=event.exact_output_usage,
            window_epoch_id=generation.window_epoch_id,
            snapshot_sequence=generation.snapshot_sequence,
            witness_ids=(event.output_usage_witness.witness_id,),
        ),
    )
    if quarantined:
        effects += (
            QuarantineRecordedEffect(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=generation.generation_reservation_id,
                reason_code="generation-usage-exceeds-allowance",
            ),
        )
    return _publish(
        state,
        next_state,
        event,
        kind=(
            AdmissionDecisionKind.QUARANTINED if quarantined else AdmissionDecisionKind.WOULD_ADMIT
        ),
        reason_code=("generation-usage-exceeds-allowance" if quarantined else "accepted"),
        capacity_changed=True,
        effects=effects,
    )


def _mark_generation_indeterminate(
    state: ContextAdmissionState,
    event: MarkGenerationIndeterminateEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    generation = _generation_record(state, event.generation_reservation_id)
    if generation is None or generation.state not in {
        GenerationState.RESERVED,
        GenerationState.STREAMING,
    }:
        return _reject(state, event, "illegal-generation-indeterminate")
    updated = replace(generation, state=GenerationState.INDETERMINATE)
    next_state = replace(
        state,
        generation_reservations=tuple(
            updated
            if record.generation_reservation_id == updated.generation_reservation_id
            else record
            for record in state.generation_reservations
        ),
    )
    return _publish(state, next_state, event)


def _request_reconciliation(
    state: ContextAdmissionState,
    event: RequestReconciliationEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    batch = (
        _batch_record(state, event.target_id)
        if isinstance(event.target_id, AdmissionBatchId)
        else None
    )
    generation = (
        _generation_record(state, event.target_id)
        if isinstance(event.target_id, GenerationReservationId)
        else None
    )
    closed_batch = (
        _closed_batch_location(state, event.target_id)
        if isinstance(event.target_id, AdmissionBatchId)
        else None
    )
    closed_generation = (
        _closed_generation_location(state, event.target_id)
        if isinstance(event.target_id, GenerationReservationId)
        else None
    )
    if not (
        (
            batch is not None
            and batch.state
            in {
                AdmissionState.RESERVED,
                AdmissionState.PREPARED,
                AdmissionState.HISTORY_STAGED,
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.INDETERMINATE,
            }
        )
        or (
            generation is not None
            and generation.state
            in {
                GenerationState.RESERVED,
                GenerationState.STREAMING,
                GenerationState.INDETERMINATE,
            }
        )
        or (
            closed_batch is not None
            and closed_batch[2].state
            in {
                AdmissionState.REQUEST_DISPATCHED,
                AdmissionState.INDETERMINATE,
            }
        )
        or (
            closed_generation is not None
            and closed_generation[2].state
            in {
                GenerationState.RESERVED,
                GenerationState.STREAMING,
                GenerationState.INDETERMINATE,
            }
        )
    ):
        return _reject(state, event, "reconciliation-target-not-unresolved")
    revision, sequence = _effect_coordinates(state, capacity_changed=False)
    effect_type = (
        ReconciliationEscalationEffect
        if "deadline" in event.reason_code.casefold()
        else ReconciliationQueryRequestedEffect
    )
    return _publish(
        state,
        state,
        event,
        effects=(
            effect_type(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=event.target_id,
                reason_code=event.reason_code,
            ),
        ),
    )


def _expire_idempotency(
    state: ContextAdmissionState,
    event: ExpireIdempotencyKeyEvent,
) -> AdmissionTransition:
    record = next(
        (
            item
            for item in state.idempotency_records
            if item.reservation_key == event.reservation_key
        ),
        None,
    )
    if record is None:
        return _reject(state, event, "idempotency-key-not-terminal")
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "idempotency-key-not-terminal")
    if any(
        tombstone.namespace == record.namespace
        and tombstone.reservation_key == event.reservation_key
        for tombstone in state.expired_idempotency_tombstones
    ):
        return _reject(state, event, "idempotency-key-expired")
    batch_record: AdmissionBatchRecord | None
    snapshot: ContextWindowSnapshot
    generation_records: tuple[GenerationReservationRecord, ...]
    if (
        event.reservation_key.window_epoch_id == state.snapshot.window_epoch_id
        and event.reservation_key.window_epoch_number == state.snapshot.window_epoch_number
    ):
        batch_record = _batch_record(
            state,
            record.original_descriptor.batch.batch_id,
        )
        snapshot = state.snapshot
        generation_records = state.generation_reservations
    else:
        audit = next(
            (
                item
                for item in state.closed_epochs
                if item.snapshot.window_epoch_id == event.reservation_key.window_epoch_id
                and item.snapshot.window_epoch_number == event.reservation_key.window_epoch_number
            ),
            None,
        )
        if audit is None:
            return _reject(state, event, "idempotency-key-not-terminal")
        batch_record = next(
            (
                item
                for item in audit.terminal_batch_records
                if item.batch.batch_id == record.original_descriptor.batch.batch_id
            ),
            None,
        )
        snapshot = audit.snapshot
        generation_records = audit.terminal_generation_reservations
    if batch_record is None or batch_record.state not in {
        AdmissionState.COMMITTED,
        AdmissionState.RELEASED,
        AdmissionState.ROLLED_BACK,
        AdmissionState.INVALIDATED,
        AdmissionState.QUARANTINED,
    }:
        return _reject(state, event, "idempotency-key-not-terminal")
    if any(
        generation.batch_id == batch_record.batch.batch_id
        and generation.state
        in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        }
        for generation in generation_records
    ):
        return _reject(state, event, "idempotency-key-not-terminal")
    if not _validate_witness_for_snapshot(
        snapshot,
        batch_record.batch,
        event.expiry_witness,
        WitnessKind.IDEMPOTENCY_EXPIRY,
    ):
        return _reject(state, event, "invalid-expiry-witness")
    tombstone = ExpiredIdempotencyTombstone(
        namespace=record.namespace,
        reservation_key=record.reservation_key,
        original_descriptor=record.original_descriptor,
        expiry_witness=event.expiry_witness,
        original_terminal_decision=record.original_reserve_decision,
    )
    next_state = replace(
        state,
        expired_idempotency_tombstones=tuple(
            sorted(
                state.expired_idempotency_tombstones + (tombstone,),
                key=lambda item: (
                    item.reservation_key.window_epoch_number,
                    item.reservation_key.batch_id.value,
                ),
            )
        ),
    )
    revision, sequence = _effect_coordinates(state, capacity_changed=False)
    reservation = record.original_descriptor.input_reservations[0]
    return _publish(
        state,
        next_state,
        event,
        effects=(
            IdempotencyExpiredEffect(
                source_event_id=event.event_id,
                resulting_aggregate_revision=revision,
                resulting_admission_sequence=sequence,
                target_id=reservation.reservation_id,
                reservation_key=event.reservation_key,
                expiry_witness_id=event.expiry_witness.witness_id,
            ),
        ),
    )


def _rollover(
    state: ContextAdmissionState,
    event: RolloverEpochEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, event, "epoch-uninitialized")
    proof = event.fence_proof
    if (
        event.witness.kind is not WitnessKind.EPOCH_ROLLOVER
        or event.witness.window_epoch_id != state.snapshot.window_epoch_id
        or event.witness.window_epoch_number != state.snapshot.window_epoch_number
        or event.witness.snapshot_sequence != state.snapshot.snapshot_sequence
        or event.new_snapshot.window_epoch_number <= state.snapshot.window_epoch_number
        or event.new_snapshot.window_epoch_id == state.snapshot.window_epoch_id
    ):
        return _reject(state, event, "invalid-rollover-witness")
    unresolved_batch_records = tuple(
        record
        for record in state.batch_records
        if record.state
        in {
            AdmissionState.REQUEST_DISPATCHED,
            AdmissionState.INDETERMINATE,
        }
    )
    retained_generation_batch_ids = {
        record.batch.batch_id
        for record in state.batch_records
        if record.state
        in {
            AdmissionState.REQUEST_DISPATCHED,
            AdmissionState.COMMITTED,
            AdmissionState.INDETERMINATE,
            AdmissionState.QUARANTINED,
        }
    }
    retained_unresolved_count = sum(
        record.unresolved_input_count
        or (
            reservation.reserved_count
            if (reservation := _reservation_for(state, record)) is not None
            else 0
        )
        for record in unresolved_batch_records
    )
    retained_generation_count = sum(
        generation.maximum_allowance
        for generation in state.generation_reservations
        if generation.batch_id in retained_generation_batch_ids
        and generation.state
        in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        }
    )
    retained_total = retained_unresolved_count + retained_generation_count
    receiver_fence_valid = proof is not None and (
        proof.old_window_epoch_id == state.snapshot.window_epoch_id
        and proof.old_window_epoch_number == state.snapshot.window_epoch_number
        and proof.new_window_epoch_id == event.new_snapshot.window_epoch_id
        and proof.new_window_epoch_number == event.new_snapshot.window_epoch_number
        and proof.receiver_authority_source_id == event.witness.authority_source_id
        and proof.highest_admitted_dispatch_sequence == _highest_dispatch_sequence(state)
    )
    fully_resolved = retained_total == 0
    snapshot_deducts_unresolved = (
        retained_total > 0
        and event.new_snapshot.remaining_count <= state.snapshot.remaining_count - retained_total
    )
    authority_alternative_valid = (
        receiver_fence_valid
        if proof is not None
        else (fully_resolved or snapshot_deducts_unresolved)
    )
    if not authority_alternative_valid:
        return _reject(state, event, "stale-receiver-fence")
    terminal_occurrences = tuple(
        replace(
            record,
            state=(
                AdmissionState.INVALIDATED
                if record.state
                in {
                    AdmissionState.PROPOSED,
                    AdmissionState.RESERVED,
                    AdmissionState.PREPARED,
                    AdmissionState.HISTORY_STAGED,
                }
                else record.state
            ),
        )
        for record in state.occurrence_records
    )
    terminal_batch_records = tuple(
        replace(
            record,
            state=(
                AdmissionState.INVALIDATED
                if record.state
                in {
                    AdmissionState.RESERVED,
                    AdmissionState.PREPARED,
                    AdmissionState.HISTORY_STAGED,
                }
                else record.state
            ),
        )
        for record in state.batch_records
    )
    invalidated_generation_reservations = tuple(
        generation
        for generation in state.generation_reservations
        if generation.state
        in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        }
        and generation.batch_id not in retained_generation_batch_ids
    )
    invalidated_generation_ids = {
        generation.generation_reservation_id for generation in invalidated_generation_reservations
    }
    terminal_generation_reservations = tuple(
        generation
        for generation in state.generation_reservations
        if generation.generation_reservation_id not in invalidated_generation_ids
    )
    audit = ClosedEpochAudit(
        snapshot=state.snapshot,
        terminal_occurrence_records=terminal_occurrences,
        terminal_batch_records=terminal_batch_records,
        terminal_reservations=state.reservations,
        terminal_generation_reservations=terminal_generation_reservations,
        closure_witness_id=event.witness.witness_id,
        fence_proof=proof,
        processed_event_tombstones=tuple(
            sorted(
                (record.event_id for record in state.processed_events),
                key=lambda event_id: event_id.value,
            )
        ),
        retained_unresolved_count=retained_unresolved_count,
        retained_generation_count=retained_generation_count,
    )
    try:
        next_state = ActiveContextAdmissionState(
            protocol_version=state.protocol_version,
            aggregate_revision=state.aggregate_revision,
            admission_sequence=state.admission_sequence,
            snapshot=event.new_snapshot,
            protected_pools=event.protected_pools,
            occurrence_records=(),
            batch_records=(),
            reservations=(),
            generation_reservations=(),
            processed_events=state.processed_events,
            idempotency_records=state.idempotency_records,
            expired_idempotency_tombstones=state.expired_idempotency_tombstones,
            closed_epochs=tuple(
                sorted(
                    state.closed_epochs + (audit,),
                    key=lambda item: item.snapshot.window_epoch_number,
                )
            ),
        )
    except ContextAdmissionValidationError:
        return _reject(state, event, "invalid-rollover-snapshot")
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
    rollover_witness_ids = _append_witness_ids(
        (),
        event.witness.witness_id,
        *((proof.fence_witness_id,) if proof is not None else ()),
    )
    invalidation_effects = tuple(
        ReservationInvalidatedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=reservation.reservation_id,
            charge_domain=ChargeDomain.INPUT_CONTEXT,
            reserve_class=reservation.reserve_class,
            protected_pool_owner_id=reservation.protected_pool_owner_id,
            count=reservation.reserved_count,
            window_epoch_id=reservation.window_epoch_id,
            snapshot_sequence=reservation.snapshot_sequence,
            witness_ids=rollover_witness_ids,
        )
        for record in state.batch_records
        if record.state
        in {
            AdmissionState.RESERVED,
            AdmissionState.PREPARED,
            AdmissionState.HISTORY_STAGED,
        }
        for reservation in state.reservations
        if reservation.reservation_id == record.reservation_id
    )
    generation_invalidation_effects = tuple(
        ReservationInvalidatedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=generation.generation_reservation_id,
            charge_domain=ChargeDomain.OUTPUT_GENERATION,
            reserve_class=generation.reserve_class,
            protected_pool_owner_id=generation.protected_pool_owner_id,
            count=generation.maximum_allowance,
            window_epoch_id=generation.window_epoch_id,
            snapshot_sequence=generation.snapshot_sequence,
            witness_ids=rollover_witness_ids,
        )
        for generation in invalidated_generation_reservations
    )
    occurrence_effects = tuple(
        OccurrenceStateChangedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=prior.occurrence.occurrence_id,
            previous_state=prior.state,
            next_state=terminal.state,
        )
        for prior, terminal in zip(
            state.occurrence_records,
            terminal_occurrences,
            strict=True,
        )
        if prior.state is not terminal.state
    )
    effects: tuple[AdmissionEffect, ...] = (
        *invalidation_effects,
        *generation_invalidation_effects,
        *occurrence_effects,
        EpochClosedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=state.snapshot.window_epoch_id,
            fence_proof=proof,
            deducted_unresolved_count=(
                retained_total if snapshot_deducts_unresolved and proof is None else 0
            ),
        ),
    )
    return _publish(
        state,
        next_state,
        event,
        capacity_changed=True,
        effects=effects,
    )


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
