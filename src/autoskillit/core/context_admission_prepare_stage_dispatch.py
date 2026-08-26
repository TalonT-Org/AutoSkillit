"""Category B — prepare/stage/dispatch handlers.

`_prepare` transitions a reserved batch through manifest/representation checks;
`_stage` records a HISTORY_STAGED witness; `_dispatch` records a
REQUEST_INCLUDED witness after a history-staged batch. All three consume
`_batch_record`, `_reservation_for`, and `_validate_witness` from
`context_admission_helpers` rather than reaching into a sibling shard.
"""

from __future__ import annotations

from dataclasses import replace

from .context_admission_helpers import (
    _append_witness_ids,
    _batch_record,
    _occurrence_effects,
    _publish,
    _reject,
    _replace_batch_record,
    _reservation_for,
    _set_occurrence_state,
    _validate_witness,
)
from .types._type_context_admission import (
    ActiveContextAdmissionState,
    AdmissionTransition,
    ContextAdmissionState,
    DispatchRequestEvent,
    PrepareBatchEvent,
    StageHistoryEvent,
)
from .types._type_enums import (
    AdmissionState,
    MeasurementKind,
    WitnessKind,
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
