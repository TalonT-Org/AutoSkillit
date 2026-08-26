"""Category E — generation reservation lifecycle handlers.

`_start_generation` transitions a generation reservation to STREAMING;
`_reconcile_closed_generation` reconciles a generation in a closed epoch;
`_reconcile_generation` reconciles an active generation reservation;
`_mark_generation_indeterminate` marks a generation as INDETERMINATE.
"""

from __future__ import annotations

from dataclasses import replace

from .context_admission_helpers import (
    _append_witness_ids,
    _batch_record,
    _closed_generation_location,
    _effect_coordinates,
    _generation_record,
    _publish,
    _reconcile_deducted_closed_charge,
    _reject,
    _replace_closed_audit,
    _validate_witness,
    _validate_witness_for_snapshot,
)
from .types._type_context_admission import (
    ActiveContextAdmissionState,
    AdmissionEffect,
    AdmissionTransition,
    ClosedEpochAudit,
    ContextAdmissionState,
    GenerationReconciledEffect,
    GenerationReservationRecord,
    MarkGenerationIndeterminateEvent,
    QuarantineRecordedEffect,
    ReconcileGenerationEvent,
    StartGenerationEvent,
)
from .types._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    ChargeDomain,
    GenerationState,
    WitnessKind,
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
