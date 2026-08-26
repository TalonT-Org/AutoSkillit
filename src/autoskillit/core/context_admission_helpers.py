"""Cross-category helpers for the context-admission reducer.

Holds every predicate, witness validation, and state mutator that more than
one dispatch-category shard needs; importing directly from this module keeps
sibling shards from reaching into each other for cross-category concerns.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .types._type_context_admission import (
    ActiveContextAdmissionState,
    AdmissionBatch,
    AdmissionBatchId,
    AdmissionBatchRecord,
    AdmissionDecision,
    AdmissionEffect,
    AdmissionOccurrenceRecord,
    AdmissionReservation,
    AdmissionReservationId,
    AdmissionTransition,
    AdmissionWitness,
    AdmissionWitnessId,
    ChargeCommittedEffect,
    ClosedEpochAudit,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextWindowSnapshot,
    DispatchRequestEvent,
    GenerationReservationId,
    GenerationReservationRecord,
    IdempotencyRecord,
    OccurrenceStateChangedEffect,
    OpenEpochEvent,
    ProcessedEventRecord,
    ProtectedPoolOwnerId,
    QuarantineRecordedEffect,
    RolloverEpochEvent,
    UninitializedContextAdmissionState,
)
from .types._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    ChargeDomain,
    ReserveClass,
    WitnessKind,
)
from .types._type_helpers import _reconciled_snapshot_counts

if TYPE_CHECKING:
    from .types._type_context_admission import (
        AcceptInputEvent,
        AdmissionSequence,
        AggregateRevision,
        ResolveIndeterminateAcceptedEvent,
    )


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
