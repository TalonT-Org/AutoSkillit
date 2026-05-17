"""Crash recovery and campaign resume logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.core import (
    FleetErrorCode,
    InfraExitCategory,
    NamedResume,
    NoResume,
    RetryReason,
    get_logger,
)
from autoskillit.fleet.state_types import (
    _ABANDON_REASONS,
    _INFRASTRUCTURE_FAILURE_REASONS,
    _VISIBLE_IN_BLOCK_STATUSES,
    FLEET_HALTED_SENTINEL,
    CampaignState,
    DispatchRecord,
    DispatchStatus,
    ResumeDecision,
)

MAX_CONSECUTIVE_RESUME_ATTEMPTS = 3

__all__ = [
    "classify_stale_dispatch",
    "derive_orchestrator_resume_spec",
    "has_blocking_dispatch",
    "has_failed_dispatch",
    "resume_campaign_from_state",
]

logger = get_logger(__name__)

_ALWAYS_BLOCKING_STATUSES = frozenset(
    {
        DispatchStatus.INTERRUPTED,
        DispatchStatus.REFUSED,
    }
)

_RETRIABLE_NON_SUCCESS = _ALWAYS_BLOCKING_STATUSES | frozenset({DispatchStatus.FAILURE})


def _count_consecutive_resumable_timeouts(history: list[dict[str, Any]]) -> int:
    """Count consecutive RESUMABLE + FLEET_L3_TIMEOUT entries from the tail."""
    count = 0
    for entry in reversed(history):
        if (
            entry.get("status") == str(DispatchStatus.RESUMABLE)
            and entry.get("reason") == FleetErrorCode.FLEET_L3_TIMEOUT
        ):
            count += 1
        else:
            break
    return count


def _is_resumable_timeout_entry(entry: dict[str, Any]) -> bool:
    return (
        entry.get("status") == str(DispatchStatus.RESUMABLE)
        and entry.get("reason") == FleetErrorCode.FLEET_L3_TIMEOUT
    )


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


def has_blocking_dispatch(state_path: Path) -> bool:
    """Check whether any dispatch should block further campaign dispatches.

    INTERRUPTED and REFUSED dispatches always block (they are retriable but not
    terminal). FAILURE blocks only when it is a logic failure (not infrastructure).

    Returns False when the file is missing or corrupted (fail-open).
    """
    from autoskillit.fleet.state import read_state  # noqa: PLC0415

    if not state_path.exists():
        return False
    state = read_state(state_path)
    if state is None:
        return False
    for d in state.dispatches:
        if d.status in _ALWAYS_BLOCKING_STATUSES:
            return True
        if d.status == DispatchStatus.FAILURE and d.reason not in _INFRASTRUCTURE_FAILURE_REASONS:
            return True
    return False


def _is_abandon_kill_metadata(kill_reason: str, infra_exit_category: str) -> bool:
    """Return True when stored kill metadata indicates resume would be futile."""
    if kill_reason in _ABANDON_REASONS:
        return True
    if (
        kill_reason == RetryReason.RESUME
        and infra_exit_category == InfraExitCategory.CONTEXT_EXHAUSTED
    ):
        return True
    return False


def classify_stale_dispatch(
    dispatch: DispatchRecord,
) -> tuple[DispatchStatus, str]:
    """Determine the recovery status for a stale RUNNING dispatch.

    Performs sidecar I/O (existence check, read) but does NOT mutate campaign
    state. The caller applies the returned status to their in-memory
    CampaignState within a CampaignStateMutator block.

    Returns (new_status, sidecar_path_or_empty).
    """
    from autoskillit.fleet.sidecar import read_sidecar_from_path  # noqa: PLC0415

    sidecar = Path(dispatch.sidecar_path) if dispatch.sidecar_path else None
    sidecar_path_str = ""
    if sidecar is not None and sidecar.exists():
        try:
            raw_lines = [ln.strip() for ln in sidecar.read_text().splitlines() if ln.strip()]
        except OSError:
            logger.warning("classify_stale_dispatch: sidecar vanished during read", exc_info=True)
        else:
            sidecar_path_str = str(sidecar)
            try:
                has_entries = bool(read_sidecar_from_path(sidecar))
            except Exception:
                logger.warning(
                    "classify_stale_dispatch: read_sidecar_from_path failed for %s",
                    sidecar,
                    exc_info=True,
                )
                # Sidecar has non-empty raw content but failed to parse — conservatively
                # treat as having entries to avoid conflating parse errors with 'no entries'.
                has_entries = bool(raw_lines)
            if not raw_lines or has_entries:
                if _is_abandon_kill_metadata(dispatch.kill_reason, dispatch.infra_exit_category):
                    return (DispatchStatus.INTERRUPTED, "")
                return (DispatchStatus.RESUMABLE, sidecar_path_str)
    logger.debug(
        "classify_stale_dispatch: no sidecar for %s — falling back to interrupted", dispatch.name
    )
    return (DispatchStatus.INTERRUPTED, "")


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
    """
    from autoskillit.fleet import is_dispatch_session_alive  # noqa: PLC0415
    from autoskillit.fleet.state import (
        CampaignStateMutator,  # noqa: PLC0415
        _clear_dispatch_for_retry,  # noqa: PLC0415
    )

    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            return None

        for d in m.state.dispatches:
            if d.status == DispatchStatus.RUNNING:
                if is_dispatch_session_alive(d):
                    continue
                new_status, sidecar_path = classify_stale_dispatch(d)
                d.status = new_status
                d.reason = "stale_running_on_resume"
                if sidecar_path:
                    d.sidecar_path = sidecar_path
                m.mark_dirty()

        if continue_on_failure and reset_on_retry:
            for d in m.state.dispatches:
                if d.status in _ALWAYS_BLOCKING_STATUSES:
                    _clear_dispatch_for_retry(d)
                    m.mark_dirty()

        for d in m.state.dispatches:
            if d.status in _RETRIABLE_NON_SUCCESS and not continue_on_failure:
                if reset_on_retry:
                    _clear_dispatch_for_retry(d)
                    m.mark_dirty()
                else:
                    return ResumeDecision(
                        next_dispatch_name="",
                        completed_dispatches_block=FLEET_HALTED_SENTINEL,
                    )

        completed_lines: list[str] = []
        next_name = ""
        is_resumable = False
        resumable_dispatched_session_id = ""
        resumable_dispatch_id = ""
        resumable_kill_reason = ""
        resumable_checkpoint: dict[str, Any] = {}
        for d in m.state.dispatches:
            if d.status in _VISIBLE_IN_BLOCK_STATUSES:
                completed_lines.append(f"- {d.name}: {d.status}")
            elif d.status == DispatchStatus.RESUMABLE and not next_name:
                timeout_count = _count_consecutive_resumable_timeouts(d.attempt_history)
                if timeout_count >= MAX_CONSECUTIVE_RESUME_ATTEMPTS:
                    # Exceeded retry budget — convert to FAILURE so campaign halts
                    d.status = DispatchStatus.FAILURE
                    d.reason = FleetErrorCode.FLEET_L3_TIMEOUT
                    m.mark_dirty()
                else:
                    next_name = d.name
                    is_resumable = True
                    resumable_dispatched_session_id = d.dispatched_session_id
                    resumable_dispatch_id = d.dispatch_id
                    resumable_kill_reason = d.kill_reason
                    resumable_checkpoint = d.resume_checkpoint
            elif (
                d.status
                not in {
                    DispatchStatus.INTERRUPTED,
                    DispatchStatus.REFUSED,
                    DispatchStatus.RUNNING,
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
            dispatch_id=resumable_dispatch_id,
            kill_reason=resumable_kill_reason,
            checkpoint=resumable_checkpoint,
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
