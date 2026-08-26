"""Category D — indeterminate and reconciliation request handlers.

`_mark_indeterminate` marks a batch as INDETERMINATE;
`_resolve_indeterminate_accepted` accepts a previously-indeterminate batch
by delegating to Category C's `_accept_closed_input` (the deterministic
closed-epoch acceptance path) — this is the one allowed cross-shard call
in the dispatcher; `_request_reconciliation` queries for reconciliation
against a batch or generation reservation.
"""

from __future__ import annotations

from dataclasses import replace

from .context_admission_accept_release import _accept_closed_input
from .context_admission_helpers import (
    _acceptance_effects,
    _accepted_effects,
    _accepted_state,
    _batch_record,
    _closed_batch_location,
    _closed_generation_location,
    _effect_coordinates,
    _generation_record,
    _occurrence_effects,
    _publish,
    _quarantined_acceptance_state,
    _reject,
    _replace_batch_record,
    _reservation_for,
    _set_occurrence_state,
    _validate_witness,
)
from .types._type_context_admission import (
    ActiveContextAdmissionState,
    AdmissionBatchId,
    AdmissionTransition,
    ContextAdmissionState,
    GenerationReservationId,
    MarkIndeterminateEvent,
    ReconciliationEscalationEffect,
    ReconciliationQueryRequestedEffect,
    RequestReconciliationEvent,
    ResolveIndeterminateAcceptedEvent,
)
from .types._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    GenerationState,
    MeasurementKind,
    WitnessKind,
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
