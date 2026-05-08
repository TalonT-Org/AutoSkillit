"""Crash recovery and campaign resume logic."""

from __future__ import annotations

import fcntl
from pathlib import Path

from autoskillit.core import InfraExitCategory, NamedResume, NoResume, RetryReason, get_logger
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
)

__all__ = [
    "crash_recover_dispatch",
    "derive_orchestrator_resume_spec",
    "has_failed_dispatch",
    "resume_campaign_from_state",
]

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
    from autoskillit.fleet.state import (  # noqa: PLC0415
        mark_dispatch_interrupted,
        mark_dispatch_resumable,
    )

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
    logger.debug(
        "crash_recover_dispatch: no sidecar for %s — falling back to interrupted",
        record.name,
    )
    try:
        mark_dispatch_interrupted(state_path, record.name, reason=reason)
        return DispatchStatus.INTERRUPTED
    except Exception:
        logger.warning(
            "crash_recover_dispatch: failed to mark dispatch interrupted", exc_info=True
        )
        return None


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
    from autoskillit.fleet import is_dispatch_session_alive  # noqa: PLC0415
    from autoskillit.fleet.state import (  # noqa: PLC0415
        _clear_dispatch_for_retry,
        _write_state,
        read_state,
    )

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


def derive_orchestrator_resume_spec(state: CampaignState) -> NamedResume | NoResume:
    """Derive the correct ResumeSpec for the L3 orchestrator from campaign state.

    Priority:
    1. state.orchestrator_session_id (if non-empty) → NamedResume
    2. Latest dispatch's caller_session_id (fallback) → NamedResume
    3. No session ID available → NoResume
    """
    if state.orchestrator_session_id:
        return NamedResume(session_id=state.orchestrator_session_id)
    for d in reversed(state.dispatches):
        if d.caller_session_id:
            return NamedResume(session_id=d.caller_session_id)
    return NoResume()
