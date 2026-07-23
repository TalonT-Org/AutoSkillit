"""Pure reducer and coverage resolver for cumulative context admission."""

from __future__ import annotations

from dataclasses import replace
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
    AdmissionOccurrenceRecord,
    AdmissionReplay,
    AdmissionReservation,
    AdmissionReservationId,
    AdmissionSequence,
    AdmissionTransition,
    AdmissionWitness,
    AggregateRevision,
    AuthorityUnavailableEffect,
    AuthorityUnavailableEvent,
    CanonicalSpanId,
    ChargeCommittedEffect,
    ClosedEpochAudit,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionValidationError,
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

__all__ = [
    "ContextAdmissionValidationError",
    "UnsupportedContextAdmissionProtocolError",
    "reduce_context_admission",
    "replay_context_admission",
    "resolve_context_admission_coverage",
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


def _accepted_effects(
    state: ActiveContextAdmissionState,
    event: AcceptInputEvent | ResolveIndeterminateAcceptedEvent,
    record: AdmissionBatchRecord,
    exact_charge: int,
    witness: AdmissionWitness,
) -> tuple[AdmissionEffect, ...]:
    reservation = _reservation_for(state, record)
    if reservation is None:
        return ()
    quarantined = (
        exact_charge > reservation.reserved_count or exact_charge > state.snapshot.hard_limit
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
            window_epoch_id=state.snapshot.window_epoch_id,
            snapshot_sequence=state.snapshot.snapshot_sequence,
            witness_ids=(witness.witness_id,),
        ),
        *_occurrence_effects(
            state,
            event,
            record.batch,
            record.state,
            AdmissionState.QUARANTINED if quarantined else AdmissionState.COMMITTED,
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
                reason_code="provider_charge_exceeds_reservation",
            ),
        )
    return effects


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
        if record.state in {AdmissionState.COMMITTED, AdmissionState.QUARANTINED}:
            charged = record.committed_input_count
        elif record.state is AdmissionState.INDETERMINATE:
            charged = record.unresolved_input_count
            if charged == 0 and reservation is not None:
                charged = reservation.reserved_count
        elif record.state in {
            AdmissionState.RESERVED,
            AdmissionState.PREPARED,
            AdmissionState.HISTORY_STAGED,
            AdmissionState.REQUEST_DISPATCHED,
        }:
            charged = reservation.reserved_count if reservation is not None else 0
        else:
            charged = 0
        global_charged += charged
        owner = record.batch.protected_pool_owner_id
        if owner is not None:
            key = (record.batch.reserve_class, owner)
            charged_by_pool[key] = charged_by_pool.get(key, 0) + charged

    for generation in state.generation_reservations:
        if generation.state in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
            GenerationState.QUARANTINED,
        }:
            global_charged += generation.maximum_allowance
            owner = generation.protected_pool_owner_id
            if owner is not None:
                key = (generation.reserve_class, owner)
                charged_by_pool[key] = charged_by_pool.get(key, 0) + generation.maximum_allowance

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
    reason_code: str,
    *,
    requested_count: int = 0,
    reserve_class: ReserveClass = ReserveClass.ORDINARY,
    protected_pool_owner_id: ProtectedPoolOwnerId | None = None,
) -> AdmissionTransition:
    return AdmissionTransition(
        next_state=state,
        decision=_decision(
            state,
            AdmissionDecisionKind.WOULD_REJECT,
            reason_code,
            requested_count=requested_count,
            reserve_class=reserve_class,
            protected_pool_owner_id=protected_pool_owner_id,
        ),
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
    published = replace(
        published,
        processed_events=published.processed_events + (processed,),
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
                    reason_code="event_replay",
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
                "event_id_conflict",
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
                    "idempotency_expired",
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
                    reason_code="reservation_key_replay",
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
                    "reservation_key_conflict",
                ),
                effects=(),
            )
    if event.expected_aggregate_revision != state.aggregate_revision:
        return _reject(state, "stale_revision")
    return None


def _batch_record(
    state: ActiveContextAdmissionState,
    batch_id: AdmissionBatchId,
) -> AdmissionBatchRecord | None:
    return next(
        (record for record in state.batch_records if record.batch.batch_id == batch_id),
        None,
    )


def _dispatched_count(state: ActiveContextAdmissionState) -> int:
    return sum(
        1 for record in state.batch_records if record.state is AdmissionState.REQUEST_DISPATCHED
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
    return replace(
        state,
        batch_records=tuple(
            updated if record.batch.batch_id == updated.batch.batch_id else record
            for record in state.batch_records
        ),
    )


def _replace_batch_record_quarantine(
    state: ActiveContextAdmissionState,
    record: AdmissionBatchRecord,
) -> tuple[AdmissionBatchRecord, ...]:
    quarantined = replace(record, state=AdmissionState.QUARANTINED)
    return tuple(
        quarantined if other.batch.batch_id == record.batch.batch_id else other
        for other in state.batch_records
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
            witness_ids += (witness.witness_id,)
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
    return (
        witness.kind is expected_kind
        and witness.window_epoch_id == state.snapshot.window_epoch_id
        and witness.window_epoch_number == state.snapshot.window_epoch_number
        and witness.snapshot_sequence == state.snapshot.snapshot_sequence
        and witness.request_id == batch.request_id
        and witness.batch_id == batch.batch_id
        and witness.representation_revision == batch.manifest.representation_revision
        and witness.occurrence_ids == batch.occurrence_ids
    )


def _open_epoch(
    state: ContextAdmissionState,
    event: OpenEpochEvent,
) -> AdmissionTransition:
    if not isinstance(state, UninitializedContextAdmissionState):
        return _reject(state, "epoch_already_active")
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
        return _reject(state, "invalid_epoch_snapshot")
    return _publish(state, active, event)


def _propose(
    state: ContextAdmissionState,
    event: ProposeOccurrenceEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, "epoch_uninitialized")
    occurrence = event.occurrence
    if (
        occurrence.lineage.window_epoch_id != state.snapshot.window_epoch_id
        or occurrence.lineage.window_epoch_number != state.snapshot.window_epoch_number
    ):
        return _reject(state, "occurrence_epoch_mismatch")
    existing = next(
        (
            record
            for record in state.occurrence_records
            if record.occurrence.occurrence_id == occurrence.occurrence_id
        ),
        None,
    )
    if existing is not None:
        kind = (
            AdmissionDecisionKind.NOOP_IDEMPOTENT
            if existing.occurrence == occurrence
            else AdmissionDecisionKind.CONFLICT
        )
        reason = (
            "occurrence_replay" if existing.occurrence == occurrence else "occurrence_conflict"
        )
        return AdmissionTransition(
            next_state=state,
            decision=_decision(state, kind, reason),
            effects=(),
        )
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
        replace(state, occurrence_records=state.occurrence_records + (record,)),
        event,
    )


def _reserve(
    state: ContextAdmissionState,
    event: ReserveRequestEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, "epoch_uninitialized")
    if event.snapshot_sequence != state.snapshot.snapshot_sequence:
        return _reject(state, "snapshot_sequence_mismatch")
    if _batch_record(state, event.batch.batch_id) is not None:
        return _reject(state, "batch_already_reserved")
    reservation = event.input_reservations[0]
    if any(
        existing.reservation_id == reservation.reservation_id for existing in state.reservations
    ):
        return _reject(state, "reservation_id_reuse_with_changed_descriptor")
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
        return _reject(state, "batch_members_not_all_proposed")
    if len(event.input_reservations) != 1:
        return _reject(state, "atomic_input_reservation_required")
    if (
        reservation.key.batch_id != event.batch.batch_id
        or reservation.occurrence_ids != event.batch.occurrence_ids
        or reservation.snapshot_sequence != state.snapshot.snapshot_sequence
        or reservation.window_epoch_id != state.snapshot.window_epoch_id
        or reservation.window_epoch_number != state.snapshot.window_epoch_number
        or reservation.reserve_class is not event.batch.reserve_class
        or reservation.protected_pool_owner_id != event.batch.protected_pool_owner_id
    ):
        return _reject(state, "reservation_descriptor_mismatch")
    expected_revisions = tuple(
        (
            record.occurrence.occurrence_id,
            record.occurrence.representation_revision,
        )
        for record in member_records
    )
    if reservation.key.occurrence_revisions != expected_revisions:
        return _reject(state, "reservation_revision_mismatch")
    generation = event.generation_reservation
    generation_count = generation.maximum_allowance if generation is not None else 0
    if generation is not None and (
        generation.request_id != event.batch.request_id
        or generation.window_epoch_id != state.snapshot.window_epoch_id
        or generation.window_epoch_number != state.snapshot.window_epoch_number
        or generation.snapshot_sequence != state.snapshot.snapshot_sequence
        or generation.reserve_class is not event.batch.reserve_class
        or generation.protected_pool_owner_id != event.batch.protected_pool_owner_id
    ):
        return _reject(state, "generation_descriptor_mismatch")
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
            "insufficient_capacity",
            requested_count=requested,
            reserve_class=event.batch.reserve_class,
            protected_pool_owner_id=event.batch.protected_pool_owner_id,
        )
    batch_record = AdmissionBatchRecord(
        batch=event.batch,
        state=AdmissionState.RESERVED,
        reservation_id=reservation.reservation_id,
        witness_ids=(),
        prepared_input_count=None,
        committed_input_count=0,
        unresolved_input_count=0,
    )
    next_state = replace(
        state,
        batch_records=state.batch_records + (batch_record,),
        reservations=state.reservations + event.input_reservations,
        generation_reservations=(
            state.generation_reservations
            + ((generation,) if generation is not None and generation_count > 0 else ())
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
    next_state = replace(
        next_state,
        idempotency_records=next_state.idempotency_records
        + (
            IdempotencyRecord(
                namespace=event.idempotency_namespace,
                reservation_key=reservation.key,
                original_descriptor=event,
                original_reserve_decision=reserve_decision,
                owning_event_id=event.event_id,
                publication_revision=type(state.aggregate_revision)(
                    state.aggregate_revision.value + 1
                ),
            ),
        ),
    )
    next_state = _set_occurrence_state(
        next_state,
        event.batch,
        AdmissionState.RESERVED,
        reservation_id=reservation.reservation_id,
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
    )


def _prepare(
    state: ContextAdmissionState,
    event: PrepareBatchEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, "epoch_uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None or record.state is not AdmissionState.RESERVED:
        return _reject(state, "illegal_prepare_order")
    if event.representation_revision != record.batch.manifest.representation_revision:
        return _reject(state, "representation_revision_mismatch")
    reservation = _reservation_for(state, record)
    if reservation is None or event.proposed_charge != reservation.reserved_count:
        return _reject(state, "prepared_charge_mismatch")
    if event.measurement_kind not in {
        MeasurementKind.PROVIDER_EXACT,
        MeasurementKind.TOKENIZER_EXACT,
    }:
        return _reject(state, "non_authoritative_measurement")
    updated = replace(
        record,
        state=AdmissionState.PREPARED,
        prepared_input_count=event.proposed_charge,
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
        return _reject(state, "epoch_uninitialized")
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
        return _reject(state, "invalid_history_stage_witness")
    updated = replace(
        record,
        state=AdmissionState.HISTORY_STAGED,
        witness_ids=record.witness_ids + (event.witness.witness_id,),
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
        return _reject(state, "epoch_uninitialized")
    record = _batch_record(state, event.batch_id)
    if (
        record is None
        or record.state not in {AdmissionState.PREPARED, AdmissionState.HISTORY_STAGED}
        or not _validate_witness(
            state,
            record.batch,
            event.witness,
            WitnessKind.REQUEST_INCLUDED,
        )
    ):
        return _reject(state, "invalid_request_inclusion_witness")
    updated = replace(
        record,
        state=AdmissionState.REQUEST_DISPATCHED,
        witness_ids=record.witness_ids + (event.witness.witness_id,),
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
        return state, AdmissionDecisionKind.QUARANTINED, "missing_reservation"
    quarantined = (
        exact_charge > reservation.reserved_count or exact_charge > state.snapshot.hard_limit
    )
    lifecycle = AdmissionState.QUARANTINED if quarantined else AdmissionState.COMMITTED
    updated = replace(
        record,
        state=lifecycle,
        witness_ids=record.witness_ids + (witness.witness_id,),
        committed_input_count=exact_charge,
        unresolved_input_count=0,
    )
    next_state = _replace_batch_record(state, updated)
    next_state = _set_occurrence_state(
        next_state,
        record.batch,
        lifecycle,
        witness=witness,
        quarantine_reason_code=("provider_charge_exceeds_reservation" if quarantined else None),
    )
    return (
        next_state,
        (AdmissionDecisionKind.QUARANTINED if quarantined else AdmissionDecisionKind.WOULD_ADMIT),
        "provider_charge_exceeds_reservation" if quarantined else "accepted",
    )


def _accept(
    state: ContextAdmissionState,
    event: AcceptInputEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, "epoch_uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None or record.state is not AdmissionState.REQUEST_DISPATCHED:
        return _reject(state, "illegal_accept_order")
    if not _validate_witness(
        state,
        record.batch,
        event.witness,
        WitnessKind.PROVIDER_ACCEPTED,
    ):
        return _reject(state, "invalid_provider_acceptance_witness")
    binding = event.representation_binding_witness
    expected_revision = record.batch.manifest.representation_revision
    if (
        event.final_manifest_revision != expected_revision
        or binding.counted_representation_revision != expected_revision
        or binding.dispatched_representation_revision != expected_revision
        or binding.final_manifest_revision != expected_revision
        or binding.request_id != record.batch.request_id
        or binding.batch_id != record.batch.batch_id
    ):
        return _reject(state, "representation_revision_mismatch")
    if event.measurement_kind not in {
        MeasurementKind.PROVIDER_EXACT,
        MeasurementKind.TOKENIZER_EXACT,
    }:
        return _reject(state, "non_authoritative_measurement")
    if event.exact_input_charge < 0:
        return _reject(state, "invalid_exact_charge")
    expected_owned_spans: set[CanonicalSpanId] = set()
    for occurrence in state.occurrence_records:
        if occurrence.occurrence.occurrence_id in set(record.batch.occurrence_ids):
            expected_owned_spans.update(occurrence.occurrence.owned_span_ids)
    manifest_spans = {owner.span_id for owner in record.batch.manifest.span_owners}
    if (
        event.authority_source_id != event.witness.authority_source_id
        or event.authority_source_id != binding.authority_source_id
    ):
        coord = _effect_coordinates(state, capacity_changed=True)
        quarantined_record = replace(
            record,
            state=AdmissionState.QUARANTINED,
            witness_ids=record.witness_ids + (event.witness.witness_id,),
            committed_input_count=event.exact_input_charge,
            unresolved_input_count=0,
        )
        next_state = _replace_batch_record(state, quarantined_record)
        next_state = _set_occurrence_state(
            next_state,
            record.batch,
            AdmissionState.QUARANTINED,
            witness=event.witness,
            quarantine_reason_code="authority_source_mismatch",
        )
        return _publish(
            state,
            next_state,
            event,
            kind=AdmissionDecisionKind.QUARANTINED,
            reason_code="authority_source_mismatch",
            requested_count=event.exact_input_charge,
            reserve_class=record.batch.reserve_class,
            protected_pool_owner_id=record.batch.protected_pool_owner_id,
            capacity_changed=True,
            effects=(
                QuarantineRecordedEffect(
                    source_event_id=event.event_id,
                    resulting_aggregate_revision=coord[0],
                    resulting_admission_sequence=coord[1],
                    target_id=record.batch.batch_id,
                    reason_code="authority_source_mismatch",
                ),
            ),
        )
    if manifest_spans != expected_owned_spans:
        coord = _effect_coordinates(state, capacity_changed=True)
        return _publish(
            state,
            replace(state, batch_records=_replace_batch_record_quarantine(state, record)),
            event,
            kind=AdmissionDecisionKind.QUARANTINED,
            reason_code="incomplete_canonical_span_ownership",
            capacity_changed=True,
            effects=(
                QuarantineRecordedEffect(
                    source_event_id=event.event_id,
                    resulting_aggregate_revision=coord[0],
                    resulting_admission_sequence=coord[1],
                    target_id=record.batch.batch_id,
                    reason_code="incomplete_canonical_span_ownership",
                ),
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
        return _reject(state, "epoch_uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None:
        return _reject(state, "unknown_batch")
    is_release = isinstance(
        event,
        ReleaseNonAdmissionEvent | ResolveIndeterminateNonAdmissionEvent,
    )
    is_resolution = isinstance(
        event,
        ResolveIndeterminateNonAdmissionEvent | ResolveIndeterminateRollbackEvent,
    )
    expected_witness = WitnessKind.NON_ADMISSION if is_release else WitnessKind.ROLLBACK
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
            AdmissionState.INDETERMINATE,
        }
    if record.state not in allowed_states or not _validate_witness(
        state,
        record.batch,
        event.witness,
        expected_witness,
    ):
        return _reject(state, "invalid_release_or_rollback_witness")
    if (
        is_release
        and record.state in {AdmissionState.HISTORY_STAGED, AdmissionState.REQUEST_DISPATCHED}
        and event.witness.kind is not WitnessKind.NON_ADMISSION
    ):
        return _reject(state, "staged_release_requires_non_admission_witness")
    lifecycle = AdmissionState.RELEASED if is_release else AdmissionState.ROLLED_BACK
    updated = replace(
        record,
        state=lifecycle,
        witness_ids=record.witness_ids + (event.witness.witness_id,),
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
        return _reject(state, "epoch_uninitialized")
    record = _batch_record(state, event.batch_id)
    if record is None or record.state not in {
        AdmissionState.PREPARED,
        AdmissionState.HISTORY_STAGED,
        AdmissionState.REQUEST_DISPATCHED,
    }:
        return _reject(state, "illegal_indeterminate_order")
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
        return _reject(state, "epoch_uninitialized")
    record = _batch_record(state, event.batch_id)
    if (
        record is None
        or record.state is not AdmissionState.INDETERMINATE
        or not _validate_witness(
            state,
            record.batch,
            event.witness,
            WitnessKind.PROVIDER_ACCEPTED,
        )
    ):
        return _reject(state, "invalid_indeterminate_acceptance")
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
        return _reject(state, "epoch_uninitialized")
    generation = _generation_record(state, event.generation_reservation_id)
    if generation is None or generation.state is not GenerationState.RESERVED:
        return _reject(state, "illegal_generation_start")
    batch_record = next(
        (
            record
            for record in state.batch_records
            if record.batch.request_id == generation.request_id
        ),
        None,
    )
    batch = batch_record.batch if batch_record is not None else None
    if (
        batch is None
        or batch_record is None
        or batch_record.state
        not in {AdmissionState.HISTORY_STAGED, AdmissionState.REQUEST_DISPATCHED}
        or not _validate_witness(
            state,
            batch,
            event.witness,
            WitnessKind.REQUEST_INCLUDED,
        )
    ):
        return _reject(state, "invalid_generation_start_witness")
    updated = replace(
        generation,
        state=GenerationState.STREAMING,
        witness_ids=generation.witness_ids + (event.witness.witness_id,),
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


def _reconcile_generation(
    state: ContextAdmissionState,
    event: ReconcileGenerationEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, "epoch_uninitialized")
    generation = _generation_record(state, event.generation_reservation_id)
    if generation is None or generation.state not in {
        GenerationState.RESERVED,
        GenerationState.STREAMING,
        GenerationState.INDETERMINATE,
    }:
        return _reject(state, "illegal_generation_reconciliation")
    if event.output_usage_witness.kind is not WitnessKind.OUTPUT_USAGE:
        return _reject(state, "invalid_output_usage_witness")
    if event.output_usage_witness.authority_source_id.value == "":
        return _reject(state, "missing_witness_authority_source")
    quarantined = event.exact_output_usage > generation.maximum_allowance
    updated = replace(
        generation,
        state=(GenerationState.QUARANTINED if quarantined else GenerationState.RECONCILED),
        exact_terminal_usage=event.exact_output_usage,
        witness_ids=generation.witness_ids + (event.output_usage_witness.witness_id,),
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
                reason_code="generation_usage_exceeds_allowance",
            ),
        )
    return _publish(
        state,
        next_state,
        event,
        kind=(
            AdmissionDecisionKind.QUARANTINED if quarantined else AdmissionDecisionKind.WOULD_ADMIT
        ),
        reason_code=("generation_usage_exceeds_allowance" if quarantined else "accepted"),
        capacity_changed=True,
        effects=effects,
    )


def _mark_generation_indeterminate(
    state: ContextAdmissionState,
    event: MarkGenerationIndeterminateEvent,
) -> AdmissionTransition:
    if not isinstance(state, ActiveContextAdmissionState):
        return _reject(state, "epoch_uninitialized")
    generation = _generation_record(state, event.generation_reservation_id)
    if generation is None or generation.state not in {
        GenerationState.RESERVED,
        GenerationState.STREAMING,
    }:
        return _reject(state, "illegal_generation_indeterminate")
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
        return _reject(state, "epoch_uninitialized")
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
    ):
        return _reject(state, "reconciliation_target_not_unresolved")
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
        return _reject(state, "idempotency_key_not_terminal")
    if isinstance(state, ActiveContextAdmissionState):
        batch_record = _batch_record(
            state,
            record.original_descriptor.batch.batch_id,
        )
        if batch_record is not None and batch_record.state not in {
            AdmissionState.COMMITTED,
            AdmissionState.RELEASED,
            AdmissionState.ROLLED_BACK,
            AdmissionState.INVALIDATED,
            AdmissionState.QUARANTINED,
        }:
            return _reject(state, "idempotency_key_not_terminal")
    if event.expiry_witness.kind is not WitnessKind.IDEMPOTENCY_EXPIRY:
        return _reject(state, "invalid_expiry_witness")
    if (
        event.expiry_witness.window_epoch_id != event.reservation_key.window_epoch_id
        or event.expiry_witness.window_epoch_number != event.reservation_key.window_epoch_number
    ):
        return _reject(state, "expiry_epoch_mismatch")
    tombstone = ExpiredIdempotencyTombstone(
        namespace=record.namespace,
        reservation_key=record.reservation_key,
        original_descriptor=record.original_descriptor,
        expiry_witness=event.expiry_witness,
        original_terminal_decision=record.original_reserve_decision,
    )
    next_state = replace(
        state,
        expired_idempotency_tombstones=(state.expired_idempotency_tombstones + (tombstone,)),
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
        return _reject(state, "epoch_uninitialized")
    proof = event.fence_proof
    if (
        event.witness.kind is not WitnessKind.EPOCH_ROLLOVER
        or proof.old_window_epoch_id != state.snapshot.window_epoch_id
        or proof.old_window_epoch_number != state.snapshot.window_epoch_number
        or proof.new_window_epoch_id != event.new_snapshot.window_epoch_id
        or proof.new_window_epoch_number != event.new_snapshot.window_epoch_number
        or proof.new_window_epoch_number <= proof.old_window_epoch_number
        or proof.receiver_authority_source_id != event.witness.authority_source_id
        or proof.highest_admitted_dispatch_sequence < len(state.reservations)
    ):
        return _reject(state, "stale_receiver_fence")
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
    retained_batch_records = tuple(
        record
        for record in state.batch_records
        if record.state
        in {
            AdmissionState.REQUEST_DISPATCHED,
            AdmissionState.INDETERMINATE,
        }
    )
    retained_reservation_ids = {
        record.reservation_id for record in retained_batch_records if record.reservation_id
    }
    retained_reservations = tuple(
        reservation
        for reservation in state.reservations
        if reservation.reservation_id in retained_reservation_ids
    )
    retained_generation_ids = {record.batch.batch_id for record in retained_batch_records}
    retained_generation_reservations = tuple(
        generation
        for generation in state.generation_reservations
        if generation.request_id in {record.batch.request_id for record in retained_batch_records}
        or generation.state
        in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        }
        and any(
            record.batch.request_id == generation.request_id
            and record.state is AdmissionState.REQUEST_DISPATCHED
            for record in retained_batch_records
        )
    )
    retained_unresolved_count = 0
    retained_generation_count = 0
    for record in state.batch_records:
        if record.state in {
            AdmissionState.REQUEST_DISPATCHED,
            AdmissionState.INDETERMINATE,
        }:
            reservation = _reservation_for(state, record)
            retained_unresolved_count += record.unresolved_input_count or (
                reservation.reserved_count if reservation is not None else 0
            )
    for generation in state.generation_reservations:
        if generation.state in {
            GenerationState.RESERVED,
            GenerationState.STREAMING,
            GenerationState.INDETERMINATE,
        } and any(request_id == generation.request_id for request_id in retained_generation_ids):
            retained_generation_count += generation.maximum_allowance
    audit = ClosedEpochAudit(
        snapshot=state.snapshot,
        terminal_occurrence_records=terminal_occurrences,
        terminal_reservations=state.reservations,
        closure_witness_id=event.witness.witness_id,
        fence_proof=proof,
        processed_event_tombstones=tuple(record.event_id for record in state.processed_events),
        retained_unresolved_count=retained_unresolved_count,
    )
    try:
        next_state = ActiveContextAdmissionState(
            protocol_version=state.protocol_version,
            aggregate_revision=state.aggregate_revision,
            admission_sequence=state.admission_sequence,
            snapshot=event.new_snapshot,
            protected_pools=event.protected_pools,
            occurrence_records=(),
            batch_records=retained_batch_records,
            reservations=retained_reservations,
            generation_reservations=retained_generation_reservations,
            processed_events=state.processed_events,
            idempotency_records=state.idempotency_records,
            expired_idempotency_tombstones=state.expired_idempotency_tombstones,
            closed_epochs=state.closed_epochs + (audit,),
        )
    except ContextAdmissionValidationError:
        return _reject(state, "invalid_rollover_snapshot")
    _ = retained_generation_count  # retained for future CapacityRecord accounting
    revision, sequence = _effect_coordinates(state, capacity_changed=True)
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
            witness_ids=(event.witness.witness_id, proof.fence_witness_id),
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
        *occurrence_effects,
        EpochClosedEffect(
            source_event_id=event.event_id,
            resulting_aggregate_revision=revision,
            resulting_admission_sequence=sequence,
            target_id=state.snapshot.window_epoch_id,
            fence_proof=proof,
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
            return AdmissionTransition(
                next_state=state,
                decision=_decision(state, kind, event.reason_code),
                effects=(),
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


def resolve_context_admission_coverage(
    surface: ProducerSurface,
    backend: str,
    configuration_mode: str,
    source_version: str,
    as_of: str,
) -> ProducerCoverageDef:
    """Resolve one static coverage row against runtime lineage inputs."""
    row = next(
        (item for item in CONTEXT_ADMISSION_COVERAGE if item.surface is surface),
        None,
    )
    if row is None:
        raise ContextAdmissionValidationError("unknown_producer_surface")
    evidence = row.evidence[0]
    matches = (
        evidence.backend == backend
        and evidence.configuration_mode == configuration_mode
        and evidence.tested_version == source_version
        and evidence.checked_at == as_of
    )
    if matches:
        return row
    return replace(
        row,
        observation_state=CoverageState.UPSTREAM_GATED,
        authority_state=CoverageState.UPSTREAM_GATED,
        reason_code="coverage_runtime_mismatch",
    )
