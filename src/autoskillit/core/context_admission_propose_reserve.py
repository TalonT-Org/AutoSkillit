"""Category A — propose/reserve dispatch handlers.

`_open_epoch` opens an epoch and seeds initial state; `_propose` registers a
new occurrence; `_reserve` records a reservation request. `_preflight` is the
dispatcher-level idempotency / replay / staleness gate that fires before any
dispatch handler from `context_admission.reduce_context_admission`.
"""

from __future__ import annotations

from dataclasses import replace

from .context_admission_helpers import (
    _batch_record,
    _capacity,
    _closed_batch_location,
    _decision,
    _effect_coordinates,
    _occurrence_effects,
    _publish,
    _reject,
)
from .types._type_context_admission import (
    ActiveContextAdmissionState,
    AdmissionBatchRecord,
    AdmissionDecision,
    AdmissionEffect,
    AdmissionOccurrenceRecord,
    AdmissionTransition,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionValidationError,
    GenerationReservationRecordedEffect,
    IdempotencyRecord,
    OpenEpochEvent,
    ProposeOccurrenceEvent,
    ReservationRecordedEffect,
    ReserveRequestEvent,
    UninitializedContextAdmissionState,
)
from .types._type_enums import (
    AdmissionDecisionKind,
    AdmissionState,
    ChargeDomain,
    ProducerSurface,
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
