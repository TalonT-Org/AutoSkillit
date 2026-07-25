"""Closed-epoch accounting helpers for cumulative context admission."""

from __future__ import annotations

from dataclasses import replace

from .types._type_context_admission import (
    ActiveContextAdmissionState,
    AdmissionBatchRecord,
    AdmissionReservation,
    ClosedEpochAudit,
)
from .types._type_enums import AdmissionState


def _closed_reservation_for(
    audit: ClosedEpochAudit,
    record: AdmissionBatchRecord,
) -> AdmissionReservation | None:
    if record.reservation_id is None:
        return None
    return next(
        (
            reservation
            for reservation in audit.terminal_reservations
            if reservation.reservation_id == record.reservation_id
        ),
        None,
    )


def _closed_retained_input_count(
    audit: ClosedEpochAudit,
    records: tuple[AdmissionBatchRecord, ...],
) -> int:
    return sum(
        record.unresolved_input_count
        or (
            reservation.reserved_count
            if (reservation := _closed_reservation_for(audit, record)) is not None
            else 0
        )
        for record in records
        if record.state
        in {
            AdmissionState.REQUEST_DISPATCHED,
            AdmissionState.INDETERMINATE,
        }
    )


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
    charge_delta = deducted_charge - terminal_charge
    if charge_delta > 0:
        capacity_slack = max(
            snapshot.hard_limit - snapshot.active_count - snapshot.remaining_count,
            0,
        )
        active_credit = min(
            max(charge_delta - capacity_slack, 0),
            snapshot.active_count,
        )
        restored_count = min(charge_delta, capacity_slack + active_credit)
        active_count = snapshot.active_count - active_credit
        remaining_count = snapshot.remaining_count + restored_count
    else:
        additional_charge = min(-charge_delta, snapshot.remaining_count)
        active_count = snapshot.active_count + additional_charge
        remaining_count = snapshot.remaining_count - additional_charge
    return replace(
        state,
        snapshot=replace(
            snapshot,
            active_count=active_count,
            remaining_count=remaining_count,
        ),
    )
