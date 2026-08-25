"""Category F — idempotency expiry and epoch rollover handlers.

Wavefront 2 (#4742) extracted these from ``core/context_admission.py``:
``_expire_idempotency`` records an idempotency-key tombstone; ``_rollover``
seals the active epoch and starts a new one with a fresh snapshot.
"""

from __future__ import annotations

from dataclasses import replace

from .context_admission_helpers import (
    _append_witness_ids,
    _batch_record,
    _effect_coordinates,
    _highest_dispatch_sequence,
    _publish,
    _reject,
    _reservation_for,
    _validate_witness_for_snapshot,
)
from .types._type_context_admission import (
    ActiveContextAdmissionState,
    AdmissionBatchRecord,
    AdmissionEffect,
    AdmissionTransition,
    ClosedEpochAudit,
    ContextAdmissionState,
    ContextAdmissionValidationError,
    ContextWindowSnapshot,
    EpochClosedEffect,
    ExpiredIdempotencyTombstone,
    ExpireIdempotencyKeyEvent,
    GenerationReservationRecord,
    IdempotencyExpiredEffect,
    OccurrenceStateChangedEffect,
    ReservationInvalidatedEffect,
    RolloverEpochEvent,
)
from .types._type_enums import (
    AdmissionState,
    ChargeDomain,
    GenerationState,
    WitnessKind,
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
