"""Gate dispatch recording."""

from __future__ import annotations

import fcntl
import time
from pathlib import Path

from autoskillit.fleet.state_types import (
    DispatchRecord,
    DispatchStatus,
    GateRecordResult,
    _resume_lock,
    _validate_transition,
)


def record_gate_outcome(
    state_path: Path,
    dispatch_name: str,
    approved: bool,
) -> GateRecordResult:
    """Record the outcome of a gate dispatch to the campaign state file.

    Returns a GateRecordResult with success/failure and error details.

    Thread-safe: _resume_lock (intra-process) + fcntl.flock(LOCK_EX)
    (cross-process) prevent concurrent callers from corrupting state.
    """
    from autoskillit.fleet.state import _write_state, read_state  # noqa: PLC0415

    with _resume_lock:
        lock_path = state_path.with_suffix(".lock")
        with open(lock_path, "wb") as _flock_handle:
            fcntl.flock(_flock_handle, fcntl.LOCK_EX)

            state = read_state(state_path)
            if state is None:
                return GateRecordResult(
                    success=False,
                    dispatch_name=dispatch_name,
                    error_code="fleet_gate_no_campaign",
                    error_message=f"Campaign state file missing or corrupted: {state_path}",
                )

            match = next((d for d in state.dispatches if d.name == dispatch_name), None)
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

            status = DispatchStatus.SUCCESS if approved else DispatchStatus.FAILURE
            now = time.time()
            new_record = DispatchRecord(
                name=dispatch_name,
                status=status,
                reason="gate_approved" if approved else "gate_rejected",
                started_at=now,
                ended_at=now,
            )
            for i, d in enumerate(state.dispatches):
                if d.name == new_record.name:
                    _validate_transition(d.status, new_record.status, d.name)
                    state.dispatches[i] = new_record
                    _write_state(state_path, state)
                    break

            return GateRecordResult(
                success=True,
                dispatch_name=dispatch_name,
                status=status.value,
            )
