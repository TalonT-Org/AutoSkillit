"""Shadow projection registry and target-construction helpers.

Owns the protocol-v1 shadow projector, the projector registry, the startup
invariant that ties the registry to :data:`CONTEXT_ADMISSION_REDUCER_REGISTRY`,
and the batch/generation shadow-target value-constructors.

Wavefront 1 of #4667.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, assert_never

from autoskillit.core import (
    CONTEXT_ADMISSION_ENCODING_VERSION,
    CONTEXT_ADMISSION_REDUCER_REGISTRY,
    AcceptInputEvent,
    ActiveContextAdmissionState,
    AdmissionBatch,
    AdmissionBatchId,
    AdmissionBatchRecord,
    AdmissionOccurrenceId,
    AdmissionReservation,
    AdmissionState,
    AdmissionTransition,
    AuthorityUnavailableEvent,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    ContextLineage,
    DispatchRequestEvent,
    ExpireIdempotencyKeyEvent,
    GenerationReservationId,
    GenerationReservationRecord,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    MeasurementKind,
    OpenEpochEvent,
    PrepareBatchEvent,
    ProposeOccurrenceEvent,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    RequestReconciliationEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    RolloverEpochEvent,
    ShadowContextAdmissionRecord,
    ShadowContextAdmissionTargetRecord,
    StageHistoryEvent,
    StartGenerationEvent,
)

_CONTEXT_ADMISSION_SHADOW_PROTOCOL_V1: Final = 1


def _shadow_record(
    stream_key: ContextAdmissionStreamKey,
    prior_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    transition: AdmissionTransition,
    journal_sequence: int,
) -> ShadowContextAdmissionRecord:
    try:
        projector = _CONTEXT_ADMISSION_SHADOW_PROJECTORS[event.protocol_version]
    except KeyError as exc:
        raise ContextAdmissionValidationError(
            "unsupported_context_admission_shadow_protocol"
        ) from exc
    return projector(stream_key, prior_state, event, transition, journal_sequence)


def _shadow_record_protocol_v1(
    stream_key: ContextAdmissionStreamKey,
    prior_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    transition: AdmissionTransition,
    journal_sequence: int,
) -> ShadowContextAdmissionRecord:
    targets = _shadow_targets(prior_state, event, transition.next_state)
    return ShadowContextAdmissionRecord(
        stream_key=stream_key,
        event_id=event.event_id,
        journal_sequence=journal_sequence,
        aggregate_revision=transition.next_state.aggregate_revision,
        admission_sequence=transition.next_state.admission_sequence,
        decision=transition.decision,
        protocol_version=event.protocol_version,
        encoding_version=CONTEXT_ADMISSION_ENCODING_VERSION,
        reason_code=transition.decision.reason_code,
        targets=tuple(
            sorted(
                targets,
                key=lambda target: (
                    type(target.target_id).__name__,
                    target.target_id.value,
                ),
            )
        ),
    )


_CONTEXT_ADMISSION_SHADOW_PROJECTORS: Final = MappingProxyType(
    {_CONTEXT_ADMISSION_SHADOW_PROTOCOL_V1: _shadow_record_protocol_v1}
)
if _CONTEXT_ADMISSION_SHADOW_PROJECTORS.keys() != CONTEXT_ADMISSION_REDUCER_REGISTRY.keys():
    raise RuntimeError("incomplete_context_admission_protocol_registry")


def _shadow_targets(
    prior_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    next_state: ContextAdmissionState,
) -> tuple[ShadowContextAdmissionTargetRecord, ...]:
    batch_ids: set[AdmissionBatchId] = set()
    generation_ids: set[GenerationReservationId] = set()
    event_batch: AdmissionBatch | None = None
    event_reservation: AdmissionReservation | None = None
    event_generation: GenerationReservationRecord | None = None
    match event:
        case OpenEpochEvent() | AuthorityUnavailableEvent() | ProposeOccurrenceEvent():
            pass
        case ReserveRequestEvent():
            batch_ids.add(event.batch.batch_id)
            event_batch = event.batch
            event_reservation = event.input_reservations[0]
            if event.generation_reservation is not None:
                generation_ids.add(event.generation_reservation.generation_reservation_id)
                event_generation = event.generation_reservation
        case (
            PrepareBatchEvent()
            | StageHistoryEvent()
            | DispatchRequestEvent()
            | AcceptInputEvent()
            | ReleaseNonAdmissionEvent()
            | RollbackAdmissionEvent()
            | MarkIndeterminateEvent()
            | ResolveIndeterminateAcceptedEvent()
            | ResolveIndeterminateNonAdmissionEvent()
            | ResolveIndeterminateRollbackEvent()
        ):
            batch_ids.add(event.batch_id)
        case (
            StartGenerationEvent()
            | ReconcileGenerationEvent()
            | MarkGenerationIndeterminateEvent()
        ):
            generation_ids.add(event.generation_reservation_id)
        case RequestReconciliationEvent():
            if isinstance(event.target_id, AdmissionBatchId):
                batch_ids.add(event.target_id)
            else:
                generation_ids.add(event.target_id)
        case ExpireIdempotencyKeyEvent():
            batch_ids.add(event.reservation_key.batch_id)
        case RolloverEpochEvent():
            if isinstance(prior_state, ActiveContextAdmissionState):
                batch_ids.update(record.batch.batch_id for record in prior_state.batch_records)
                generation_ids.update(
                    record.generation_reservation_id
                    for record in prior_state.generation_reservations
                )
        case _ as unreachable:
            assert_never(unreachable)
    targets: list[ShadowContextAdmissionTargetRecord] = []
    for batch_id in sorted(batch_ids, key=lambda item: item.value):
        target = _input_shadow_target(
            prior_state,
            next_state,
            event,
            batch_id,
            event_batch=event_batch,
            event_reservation=event_reservation,
        )
        if target is not None:
            targets.append(target)
    for generation_id in sorted(generation_ids, key=lambda item: item.value):
        target = _generation_shadow_target(
            prior_state,
            next_state,
            event,
            generation_id,
            event_generation=event_generation,
        )
        if target is not None:
            targets.append(target)
    return tuple(targets)


def _input_shadow_target(
    prior_state: ContextAdmissionState,
    next_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    batch_id: AdmissionBatchId,
    *,
    event_batch: AdmissionBatch | None,
    event_reservation: AdmissionReservation | None,
) -> ShadowContextAdmissionTargetRecord | None:
    record, reservation = _find_batch(next_state, batch_id)
    if record is None:
        record, reservation = _find_batch(prior_state, batch_id)
    batch_value = record.batch if record is not None else event_batch
    reservation = reservation or event_reservation
    if batch_value is None:
        return None
    lineages = _find_lineages(
        next_state,
        prior_state,
        batch_value.occurrence_ids,
    )
    if lineages is None:
        return None
    lifecycle_state = (
        record.state
        if record is not None
        else _prior_occurrence_state(prior_state, batch_value.occurrence_ids)
    )
    exact_input_charge: int | None = None
    measurement_kind: MeasurementKind | None = None
    if isinstance(event, AcceptInputEvent) and event.batch_id == batch_id:
        exact_input_charge = event.exact_input_charge
        measurement_kind = event.measurement_kind
    elif isinstance(event, ResolveIndeterminateAcceptedEvent) and event.batch_id == batch_id:
        exact_input_charge = event.exact_charge
        measurement_kind = event.measurement_kind
    elif isinstance(event, PrepareBatchEvent) and event.batch_id == batch_id:
        measurement_kind = event.measurement_kind
    return ShadowContextAdmissionTargetRecord(
        target_id=batch_id,
        occurrence_ids=batch_value.occurrence_ids,
        turn_ids=tuple(lineage.turn_id for lineage in lineages),
        tool_call_ids=tuple(lineage.tool_call_id for lineage in lineages),
        producer_instance_ids=tuple(lineage.producer_instance_id for lineage in lineages),
        producer_surfaces=tuple(lineage.producer_surface for lineage in lineages),
        delivery_occurrence_ids=tuple(lineage.delivery_occurrence_id for lineage in lineages),
        reservation_id=(reservation.reservation_id if reservation is not None else None),
        batch_id=batch_id,
        generation_reservation_id=None,
        window_epoch_id=(
            reservation.window_epoch_id if reservation is not None else lineages[0].window_epoch_id
        ),
        reserve_class=batch_value.reserve_class,
        lifecycle_state=lifecycle_state,
        proposed_input_count=(reservation.reserved_count if reservation is not None else None),
        generation_allowance=None,
        exact_input_charge=exact_input_charge,
        exact_output_charge=None,
        measurement_kind=measurement_kind,
    )


def _generation_shadow_target(
    prior_state: ContextAdmissionState,
    next_state: ContextAdmissionState,
    event: ContextAdmissionEvent,
    generation_id: GenerationReservationId,
    *,
    event_generation: GenerationReservationRecord | None,
) -> ShadowContextAdmissionTargetRecord | None:
    record = _find_generation(next_state, generation_id)
    if record is None:
        record = _find_generation(prior_state, generation_id)
    record = record or event_generation
    if record is None:
        return None
    lineages = _find_lineages(
        next_state,
        prior_state,
        record.occurrence_ids,
    )
    if lineages is None:
        return None
    batch_record, reservation = _find_batch(next_state, record.batch_id)
    if batch_record is None:
        batch_record, reservation = _find_batch(prior_state, record.batch_id)
    reconciliation = (
        event
        if isinstance(event, ReconcileGenerationEvent)
        and event.generation_reservation_id == generation_id
        else None
    )
    exact_output_charge = reconciliation.exact_output_usage if reconciliation is not None else None
    return ShadowContextAdmissionTargetRecord(
        target_id=generation_id,
        occurrence_ids=record.occurrence_ids,
        turn_ids=tuple(lineage.turn_id for lineage in lineages),
        tool_call_ids=tuple(lineage.tool_call_id for lineage in lineages),
        producer_instance_ids=tuple(lineage.producer_instance_id for lineage in lineages),
        producer_surfaces=tuple(lineage.producer_surface for lineage in lineages),
        delivery_occurrence_ids=tuple(lineage.delivery_occurrence_id for lineage in lineages),
        reservation_id=(reservation.reservation_id if reservation is not None else None),
        batch_id=record.batch_id,
        generation_reservation_id=generation_id,
        window_epoch_id=record.window_epoch_id,
        reserve_class=record.reserve_class,
        lifecycle_state=record.state,
        proposed_input_count=None,
        generation_allowance=record.maximum_allowance,
        exact_input_charge=None,
        exact_output_charge=exact_output_charge,
        measurement_kind=(MeasurementKind.PROVIDER_EXACT if reconciliation is not None else None),
    )


def _find_batch(
    state: ContextAdmissionState,
    batch_id: AdmissionBatchId,
) -> tuple[AdmissionBatchRecord | None, AdmissionReservation | None]:
    if isinstance(state, ActiveContextAdmissionState):
        record = next(
            (item for item in state.batch_records if item.batch.batch_id == batch_id),
            None,
        )
        if record is not None:
            reservation = next(
                (
                    item
                    for item in state.reservations
                    if item.reservation_id == record.reservation_id
                ),
                None,
            )
            return record, reservation
    for audit in state.closed_epochs:
        record = next(
            (item for item in audit.terminal_batch_records if item.batch.batch_id == batch_id),
            None,
        )
        if record is not None:
            return record, audit.reservation_for(record)
    return None, None


def _find_generation(
    state: ContextAdmissionState,
    generation_id: GenerationReservationId,
) -> GenerationReservationRecord | None:
    if isinstance(state, ActiveContextAdmissionState):
        record = next(
            (
                item
                for item in state.generation_reservations
                if item.generation_reservation_id == generation_id
            ),
            None,
        )
        if record is not None:
            return record
    return next(
        (
            item
            for audit in state.closed_epochs
            for item in audit.terminal_generation_reservations
            if item.generation_reservation_id == generation_id
        ),
        None,
    )


def _find_lineages(
    primary_state: ContextAdmissionState,
    fallback_state: ContextAdmissionState,
    occurrence_ids: tuple[AdmissionOccurrenceId, ...],
) -> tuple[ContextLineage, ...] | None:
    records = {}
    for state in (fallback_state, primary_state):
        if isinstance(state, ActiveContextAdmissionState):
            records.update(
                {
                    record.occurrence.occurrence_id: record.occurrence.lineage
                    for record in state.occurrence_records
                }
            )
        for audit in state.closed_epochs:
            records.update(
                {
                    record.occurrence.occurrence_id: record.occurrence.lineage
                    for record in audit.terminal_occurrence_records
                }
            )
    if any(occurrence_id not in records for occurrence_id in occurrence_ids):
        return None
    return tuple(records[occurrence_id] for occurrence_id in occurrence_ids)


def _prior_occurrence_state(
    state: ContextAdmissionState,
    occurrence_ids: tuple[AdmissionOccurrenceId, ...],
) -> AdmissionState:
    if isinstance(state, ActiveContextAdmissionState):
        states = {
            record.state
            for record in state.occurrence_records
            if record.occurrence.occurrence_id in occurrence_ids
        }
        if len(states) == 1:
            return states.pop()
    return AdmissionState.PROPOSED


__all__ = [
    "_shadow_record",
    "_shadow_record_protocol_v1",
    "_CONTEXT_ADMISSION_SHADOW_PROJECTORS",
    "_CONTEXT_ADMISSION_SHADOW_PROTOCOL_V1",
    "_shadow_targets",
    "_input_shadow_target",
    "_generation_shadow_target",
    "_find_batch",
    "_find_generation",
    "_find_lineages",
    "_prior_occurrence_state",
]
