"""Crash recovery and campaign resume logic."""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path

from autoskillit.core import InfraExitCategory, RetryReason, get_logger, write_versioned_json
from autoskillit.fleet.state_types import (
    _ABANDON_KILL_REASONS,
    _INFRASTRUCTURE_FAILURE_REASONS,
    _VISIBLE_IN_BLOCK_STATUSES,
    FLEET_HALTED_SENTINEL,
    CampaignState,
    DispatchRecord,
    DispatchStatus,
    ResumeDecision,
    _resume_lock,
    _validate_transition,
)

logger = get_logger(__name__)


def has_failed_dispatch(state_path: Path) -> bool:
    """Check whether any dispatch has a FAILURE status attributable to logic (not infrastructure).

    Infrastructure failures (e.g. fleet_l3_no_result_block) represent transient L3
    disconnections and do not halt the campaign. Logic failures (e.g. completed_clean
    with success=false) represent genuine task failures and do halt the campaign.

    Returns False when the file is missing or corrupted (fail-open).
    """
    from autoskillit.fleet.state import read_state  # noqa: PLC0415

    if not state_path.exists():
        return False
    state = read_state(state_path)
    if state is None:
        return False
    return any(
        d.status == DispatchStatus.FAILURE and d.reason not in _INFRASTRUCTURE_FAILURE_REASONS
        for d in state.dispatches
    )


def _is_abandon_kill_reason(kill_reason: str, infra_exit_category: str) -> bool:
    """Check if stored kill metadata indicates resume would be futile."""
    if kill_reason in _ABANDON_KILL_REASONS:
        return True
    if (
        kill_reason == RetryReason.RESUME
        and infra_exit_category == InfraExitCategory.CONTEXT_EXHAUSTED
    ):
        return True
    return False


def crash_recover_dispatch(
    state_path: Path,
    record: DispatchRecord,
    reason: str = "stale_running_on_resume",
) -> DispatchStatus | None:
    """Recover a stale RUNNING dispatch to RESUMABLE or INTERRUPTED; None if both writes fail."""
    from autoskillit.fleet.sidecar import read_sidecar_from_path  # noqa: PLC0415

    sidecar = Path(record.sidecar_path) if record.sidecar_path else None
    if sidecar is not None and sidecar.exists():
        try:
            raw_lines = [ln.strip() for ln in sidecar.read_text().splitlines() if ln.strip()]
        except OSError:
            logger.warning("crash_recover_dispatch: sidecar vanished during read", exc_info=True)
        else:
            if not raw_lines or read_sidecar_from_path(sidecar):
                if _is_abandon_kill_reason(record.kill_reason, record.infra_exit_category):
                    try:
                        mark_dispatch_interrupted(state_path, record.name, reason=reason)
                        return DispatchStatus.INTERRUPTED
                    except Exception:
                        logger.warning(
                            "crash_recover_dispatch: failed to mark dispatch interrupted",
                            exc_info=True,
                        )
                        return None
                try:
                    mark_dispatch_resumable(state_path, record.name, sidecar_path=str(sidecar))
                    return DispatchStatus.RESUMABLE
                except Exception:
                    logger.warning(
                        "crash_recover_dispatch: failed to mark dispatch resumable",
                        exc_info=True,
                    )
    try:
        mark_dispatch_interrupted(state_path, record.name, reason=reason)
        return DispatchStatus.INTERRUPTED
    except Exception:
        logger.warning(
            "crash_recover_dispatch: failed to mark dispatch interrupted", exc_info=True
        )
        return None


def mark_dispatch_interrupted(
    state_path: Path,
    dispatch_name: str,
    *,
    reason: str,
) -> None:
    """Atomically mark a dispatch as interrupted with a reason."""
    state = read_state(state_path)
    if state is None:
        raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
    for d in state.dispatches:
        if d.name == dispatch_name:
            _validate_transition(d.status, DispatchStatus.INTERRUPTED, d.name)
            d.status = DispatchStatus.INTERRUPTED
            d.reason = reason
            d.ended_at = time.time()
            break
    else:
        raise ValueError(f"Dispatch '{dispatch_name}' not found in state")
    _write_state(state_path, state)


def mark_dispatch_resumable(
    state_path: Path,
    dispatch_name: str,
    *,
    sidecar_path: str,
) -> None:
    """Atomically transition a RUNNING dispatch to RESUMABLE, preserving the sidecar path."""
    state = read_state(state_path)
    if state is None:
        raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
    for d in state.dispatches:
        if d.name == dispatch_name:
            _validate_transition(d.status, DispatchStatus.RESUMABLE, d.name)
            d.status = DispatchStatus.RESUMABLE
            d.sidecar_path = sidecar_path
            d.ended_at = time.time()
            break
    else:
        raise ValueError(f"Dispatch '{dispatch_name}' not found in state")
    _write_state(state_path, state)


def resume_campaign_from_state(
    state_path: Path,
    continue_on_failure: bool,
    *,
    reset_on_retry: bool = False,
) -> ResumeDecision | None:
    """Determine the next dispatch for a resumed campaign.

    Algorithm:
      1. Read state.json; return None if absent or corrupted.
      2. Find first dispatch not in {success, skipped}.
      3. If running exists and stale, mark it interrupted; skip alive ones.
      4. If failure exists and continue_on_failure=False, return None
         with reason fleet_halted_on_failure (encoded via a sentinel).
         When reset_on_retry=True, reset all FAILURE dispatches to PENDING instead.
      5. Return ResumeDecision with next_dispatch_name and completed block.

    Returns None if the state file is missing/corrupted. Returns a
    ResumeDecision with next_dispatch_name="" if all dispatches are
    complete or the campaign is halted.

    Thread-safe: _resume_lock (intra-process) + fcntl.flock(LOCK_EX)
    (cross-process) prevent concurrent callers from corrupting state.
    """
    from autoskillit.fleet import is_dispatch_session_alive

    with _resume_lock:
        lock_path = state_path.with_suffix(".lock")
        with open(lock_path, "wb") as _flock_handle:
            fcntl.flock(_flock_handle, fcntl.LOCK_EX)

            state = read_state(state_path)
            if state is None:
                return None

            for d in state.dispatches:
                if d.status == DispatchStatus.RUNNING:
                    if is_dispatch_session_alive(d):
                        continue
                    new_status = crash_recover_dispatch(state_path, d)
                    if new_status is not None:
                        d.status = new_status
                        d.reason = "stale_running_on_resume"

            did_reset = False
            for d in state.dispatches:
                if d.status == DispatchStatus.FAILURE and not continue_on_failure:
                    if reset_on_retry:
                        _clear_dispatch_for_retry(d)
                        did_reset = True
                    else:
                        return ResumeDecision(
                            next_dispatch_name="",
                            completed_dispatches_block=FLEET_HALTED_SENTINEL,
                        )
            if did_reset:
                _write_state(state_path, state)

            completed_lines: list[str] = []
            next_name = ""
            is_resumable = False
            resumable_dispatched_session_id = ""
            resumable_kill_reason = ""
            for d in state.dispatches:
                if d.status in _VISIBLE_IN_BLOCK_STATUSES:
                    completed_lines.append(f"- {d.name}: {d.status}")
                elif d.status == DispatchStatus.RESUMABLE and not next_name:
                    next_name = d.name
                    is_resumable = True
                    resumable_dispatched_session_id = d.dispatched_session_id
                    resumable_kill_reason = d.kill_reason
                elif (
                    d.status
                    not in {
                        DispatchStatus.INTERRUPTED,
                        DispatchStatus.RUNNING,
                        DispatchStatus.REFUSED,
                        DispatchStatus.RELEASED,
                        DispatchStatus.FAILURE,
                        DispatchStatus.RESUMABLE,
                    }
                    and not next_name
                ):
                    next_name = d.name

            completed_block = "\n".join(completed_lines) if completed_lines else ""

            return ResumeDecision(
                next_dispatch_name=next_name,
                completed_dispatches_block=completed_block,
                is_resumable=is_resumable,
                dispatched_session_id=resumable_dispatched_session_id,
                kill_reason=resumable_kill_reason,
            )


def _clear_dispatch_for_retry(d: DispatchRecord) -> None:
    """Clear a dispatch record for retry."""
    _validate_transition(d.status, DispatchStatus.PENDING, d.name)
    d.status = DispatchStatus.PENDING
    d.reason = ""
    d.dispatch_id = ""
    d.dispatched_session_id = ""
    d.dispatched_session_log_dir = ""
    d.dispatched_pid = 0
    d.dispatched_starttime_ticks = 0
    d.dispatched_boot_id = ""
    d.token_usage = {}
    d.started_at = 0.0
    d.ended_at = 0.0
    d.sidecar_path = None


def _write_state(state_path: Path, state: CampaignState) -> None:
    """Internal: atomic write of full state to disk."""
    payload = {
        "campaign_id": state.campaign_id,
        "campaign_name": state.campaign_name,
        "manifest_path": state.manifest_path,
        "started_at": state.started_at,
        "dispatches": [d.to_dict() for d in state.dispatches],
        "captured_values": state.captured_values,
    }
    write_versioned_json(state_path, payload, schema_version=state.schema_version)


def read_state(state_path: Path) -> CampaignState | None:
    """Load campaign state from disk."""
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        dispatches = [DispatchRecord.from_dict(d) for d in data["dispatches"]]
        return CampaignState(
            schema_version=data["schema_version"],
            campaign_id=data["campaign_id"],
            campaign_name=data["campaign_name"],
            manifest_path=data["manifest_path"],
            started_at=data["started_at"],
            dispatches=dispatches,
            captured_values=data.get("captured_values", {}),
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("read_state: schema mismatch or corrupt payload in %s: %s", state_path, exc)
        return None
