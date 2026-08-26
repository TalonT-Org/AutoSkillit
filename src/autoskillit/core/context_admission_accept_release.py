"""Category C — accept/release dispatch handlers.

`_accept_closed_input` handles acceptance of a closed-epoch batch;
`_accept` handles active-batch acceptance; `_release_closed_batch` and
`_release_or_rollback` release or rollback a batch in the active or
closed-epoch state. These also serve as the reduce target for
`_resolve_indeterminate_accepted` (Category D), so the closed-epoch
acceptance path doubles as the canonical resolution entry point.
"""

from __future__ import annotations

from dataclasses import replace

from .context_admission_helpers import (
    _acceptance_effects,
    _accepted_effects,
    _accepted_state,
    _append_witness_ids,
    _batch_record,
    _closed_batch_location,
    _effect_coordinates,
    _occurrence_effects,
    _publish,
    _quarantined_acceptance_state,
    _reconcile_deducted_closed_charge,
    _reject,
    _replace_batch_record,
    _replace_closed_audit,
    _required_release_witness_kind,
    _reservation_for,
    _set_occurrence_state,
    _validate_witness,
    _validate_witness_for_snapshot,
)
from .types._type_context_admission import (
    AcceptInputEvent,
    ActiveContextAdmissionState,
    AdmissionBatchRecord,
    AdmissionEffect,
    AdmissionOccurrenceId,
    AdmissionTransition,
    CanonicalSpanId,
    ChargeCommittedEffect,
    ClosedEpochAudit,
    ContextAdmissionState,
    GenerationReservationRecord,
    QuarantineRecordedEffect,
    ReleaseNonAdmissionEvent,
    ReservationInvalidatedEffect,
    ReservationReleasedEffect,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
)
from .types._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    ChargeDomain,
    GenerationState,
    MeasurementKind,
    WitnessKind,
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
