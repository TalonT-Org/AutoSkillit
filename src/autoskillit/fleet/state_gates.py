"""Gate dispatch recording."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from autoskillit.fleet.state_types import (
    DispatchStatus,
    GateRecordResult,
    _validate_transition,
)


def record_gate_outcome(
    state_path: Path,
    dispatch_name: str,
    approved: bool,
) -> GateRecordResult:
    """Record the outcome of a gate dispatch to the campaign state file.

    Returns a GateRecordResult with success/failure and error details.
    """
    from autoskillit.fleet.state import CampaignStateMutator  # noqa: PLC0415

    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            return GateRecordResult(
                success=False,
                dispatch_name=dispatch_name,
                error_code="fleet_gate_no_campaign",
                error_message=f"Campaign state file missing or corrupted: {state_path}",
            )

        match = next((d for d in m.state.dispatches if d.name == dispatch_name), None)
        if match is None:
            return GateRecordResult(
                success=False,
                dispatch_name=dispatch_name,
                error_code="fleet_gate_unknown_dispatch",
                error_message=f"Dispatch '{dispatch_name}' not found in campaign state.",
            )

        if match.status != DispatchStatus.PENDING:
            return GateRecordResult(
                success=False,
                dispatch_name=dispatch_name,
                error_code="fleet_gate_already_recorded",
                error_message=(
                    f"Dispatch '{dispatch_name}' is already {match.status.value}, not PENDING."
                ),
            )

        status = DispatchStatus.SUCCESS if approved else DispatchStatus.REFUSED
        now = time.time()
        new_record = replace(
            match,
            status=status,
            reason="gate_approved" if approved else "gate_rejected",
            started_at=now,
            ended_at=now,
        )
        for i, d in enumerate(m.state.dispatches):
            if d.name == new_record.name:
                _validate_transition(d.status, new_record.status, d.name)
                m.state.dispatches[i] = new_record
                m.mark_dirty()
                break
        else:
            return GateRecordResult(
                success=False,
                dispatch_name=dispatch_name,
                error_code="fleet_gate_dispatch_vanished",
                error_message=(
                    f"Dispatch '{dispatch_name}' was present at pre-check"
                    " but absent during mutation."
                ),
            )

        return GateRecordResult(
            success=True,
            dispatch_name=dispatch_name,
            status=status.value,
        )
