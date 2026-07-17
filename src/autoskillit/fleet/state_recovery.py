"""Crash recovery and campaign resume logic."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

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
    TERMINAL_UNCLEANED_STATUSES,
    CampaignState,
    DispatchRecord,
    DispatchStatus,
    ResumeDecision,
)

MAX_CONSECUTIVE_RESUME_ATTEMPTS = 3

if TYPE_CHECKING:
    from autoskillit.fleet.state import CampaignStateMutator


class ResumePreflight(NamedTuple):
    """Result of the ``prepare_resume`` precondition chokepoint.

    Fields:
        prior_session_chain: Session chain read from the prior record (preserved
            even when reset_performed is True).
        prior_dispatched_session_id: Last-session-id for chain fallback lookups.
        short_circuit: Populated when the prior dispatch is SUCCESS — caller
            can short-circuit to ``_build_success_short_circuit``.
        reset_performed: True when the chokepoint auto-reset a blocking status
            (FAILURE/INTERRUPTED/REFUSED) to PENDING via ``reset_blocking_dispatch``.
        halt: True when ``continue_on_failure=False`` and the prior status was
            terminal — caller must refuse the resume.
        halted_reason: Populated when halt=True; describes why the resume was
            refused.
    """

    prior_session_chain: list[str]
    prior_dispatched_session_id: str
    short_circuit: DispatchRecord | None
    reset_performed: bool
    halt: bool
    halted_reason: str | None


__all__ = [
    "MAX_CONSECUTIVE_RESUME_ATTEMPTS",
    "ResumePreflight",
    "classify_stale_dispatch",
    "derive_orchestrator_resume_spec",
    "find_completed_dispatch",
    "find_dispatch_for_issue",
    "has_blocking_dispatch",
    "has_completed_dispatch",
    "has_failed_dispatch",
    "prepare_resume",
    "resolve_stale_running",
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


_RESUMABLE_RETRY_REASONS: frozenset[str] = frozenset(
    {
        FleetErrorCode.FLEET_L3_TIMEOUT,
        FleetErrorCode.FLEET_QUOTA_EXHAUSTED,
        FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
    }
)

_PENDING_QUIET_PERIOD_SECONDS: float = 60.0


def _count_consecutive_resumable_timeouts(history: list[dict[str, Any]]) -> int:
    """Count consecutive RESUMABLE entries with retriable reasons from the tail."""
    count = 0
    for entry in reversed(history):
        if (
            entry.get("status") == str(DispatchStatus.RESUMABLE)
            and entry.get("reason") in _RESUMABLE_RETRY_REASONS
        ):
            count += 1
        else:
            break
    return count


def prepare_resume(
    state_path: Path,
    dispatch_name: str,
    *,
    continue_on_failure: bool = True,
) -> ResumePreflight | None:
    """Single precondition chokepoint for every resume entry point.

    Returns:
        - ``None`` if the state file is missing or corrupt (caller treats as
          a fail-open: fall through to fresh dispatch).
        - ``ResumePreflight`` with ``short_circuit`` set when the prior dispatch
          is SUCCESS (caller can return ``_build_success_short_circuit``).
        - ``ResumePreflight`` with ``reset_performed=True`` when the prior
          dispatch was in ``{FAILURE, INTERRUPTED, REFUSED}`` and was
          auto-reset to PENDING via ``reset_blocking_dispatch``.
        - ``ResumePreflight`` with ``halt=True`` when ``continue_on_failure``
          is False and the prior dispatch was in a terminal status
          (campaign-level halt semantic preserved).
        - ``ResumePreflight`` with ``prior_session_chain`` populated when the
          dispatch is in ``PENDING`` or ``RESUMABLE`` (caller proceeds with
          existing resume flow; cap enforcement happens in
          ``mark_dispatch_running``).
        - ``ResumePreflight`` with all-empty fields when no dispatch matches
          ``dispatch_name`` (caller treats as a fresh dispatch under the named
          slot).

    The cap check (``MAX_CONSECUTIVE_RESUME_ATTEMPTS``) is intentionally NOT
    performed here — it's owned by ``mark_dispatch_running`` so the same
    cap semantics apply regardless of the entry point that triggered the
    transition.
    """
    from autoskillit.fleet.state import read_state, reset_blocking_dispatch

    if not state_path.exists():
        return None
    state = read_state(state_path)
    if state is None:
        return None

    target: DispatchRecord | None = None
    for d in state.dispatches:
        if d.name == dispatch_name:
            target = d
            break
    if target is None:
        # No matching dispatch — caller can treat as fresh.
        return ResumePreflight(
            prior_session_chain=[],
            prior_dispatched_session_id="",
            short_circuit=None,
            reset_performed=False,
            halt=False,
            halted_reason=None,
        )

    prior_session_chain = list(target.session_chain)
    prior_dispatched_session_id = target.dispatched_session_id

    # Case 1: SUCCESS — short-circuit the resume (caller can reuse prior result).
    if target.status == DispatchStatus.SUCCESS:
        return ResumePreflight(
            prior_session_chain=prior_session_chain,
            prior_dispatched_session_id=prior_dispatched_session_id,
            short_circuit=target,
            reset_performed=False,
            halt=False,
            halted_reason=None,
        )

    # Case 2: Resettable terminal status — auto-reset OR halt.
    # _RETRIABLE_NON_SUCCESS == {FAILURE, INTERRUPTED, REFUSED} matches the
    # canonical _RESETTABLE_STATUSES set in fleet._reset without forcing a
    # module-level import (which would re-trigger the cycle through state.py).
    if target.status in _RETRIABLE_NON_SUCCESS:
        if not continue_on_failure:
            return ResumePreflight(
                prior_session_chain=prior_session_chain,
                prior_dispatched_session_id=prior_dispatched_session_id,
                short_circuit=None,
                reset_performed=False,
                halt=True,
                halted_reason=(
                    f"Campaign halted: prior dispatch {dispatch_name!r} is in "
                    f"{target.status.value!r} and continue_on_failure is false"
                ),
            )
        # Auto-reset blocking status → PENDING via the canonical helper.
        reset_blocking_dispatch(state_path, dispatch_name)
        return ResumePreflight(
            prior_session_chain=prior_session_chain,
            prior_dispatched_session_id=prior_dispatched_session_id,
            short_circuit=None,
            reset_performed=True,
            halt=False,
            halted_reason=None,
        )

    # Case 3: PENDING / RESUMABLE / SKIPPED / RELEASED — pass through. Cap
    # enforcement is delegated to mark_dispatch_running (layer L3).
    return ResumePreflight(
        prior_session_chain=prior_session_chain,
        prior_dispatched_session_id=prior_dispatched_session_id,
        short_circuit=None,
        reset_performed=False,
        halt=False,
        halted_reason=None,
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


def has_completed_dispatch(state_path: Path, dispatch_name: str) -> bool:
    """Return True if the named dispatch already has SUCCESS status.

    Returns False when the file is missing or corrupted (fail-open).
    """
    return find_completed_dispatch(state_path, dispatch_name) is not None


def find_completed_dispatch(state_path: Path, dispatch_name: str) -> DispatchRecord | None:
    """Return the DispatchRecord if the named dispatch has SUCCESS status.

    Returns None when the file is missing, corrupted, or no matching SUCCESS
    record exists (fail-open).
    """
    from autoskillit.fleet.state import read_state  # noqa: PLC0415

    if not state_path.exists():
        return None
    state = read_state(state_path)
    if state is None:
        return None
    for d in state.dispatches:
        if d.name == dispatch_name and d.status == DispatchStatus.SUCCESS:
            return d
    return None


def _is_abandon_kill_metadata(retry_reason: str, infra_exit_category: str) -> bool:
    """Return True when stored kill metadata indicates resume would be futile."""
    if retry_reason in _ABANDON_REASONS:
        return True
    if (
        retry_reason == RetryReason.RESUME
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
    from autoskillit.fleet.sidecar import (  # noqa: PLC0415
        SidecarReadStatus,
        read_sidecar_from_path,
    )

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
                sidecar_result = read_sidecar_from_path(sidecar)
                has_entries = sidecar_result.source == SidecarReadStatus.FOUND and bool(
                    sidecar_result.entries
                )
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
                if _is_abandon_kill_metadata(dispatch.retry_reason, dispatch.infra_exit_category):
                    return (DispatchStatus.INTERRUPTED, "")
                return (DispatchStatus.RESUMABLE, sidecar_path_str)
    logger.debug(
        "classify_stale_dispatch: no sidecar for %s — falling back to interrupted", dispatch.name
    )
    return (DispatchStatus.INTERRUPTED, "")


def resolve_stale_running(
    dispatch: DispatchRecord,
    mutator: CampaignStateMutator,
    *,
    reason: str = "stale_running_resolved",
) -> bool:
    """Check whether a RUNNING dispatch is actually alive.

    Returns True if the dispatch is confirmed alive (caller should block).
    Returns False if the dispatch is dead — reclassifies it in-place within
    the mutator (to INTERRUPTED or RESUMABLE via classify_stale_dispatch)
    and marks the mutator dirty. Caller can proceed with the demoted status.

    Precondition: dispatch.status == DispatchStatus.RUNNING.
    Precondition: dispatch is an element of mutator.state.dispatches
                  (not a copy from an unlocked read).
    Precondition: called within a CampaignStateMutator context.
    """
    from autoskillit.fleet import is_dispatch_session_alive

    if is_dispatch_session_alive(dispatch):
        return True

    new_status, sidecar_path = classify_stale_dispatch(dispatch)
    dispatch.status = new_status
    dispatch.reason = reason
    if sidecar_path:
        dispatch.sidecar_path = sidecar_path
    mutator.mark_dirty()
    return False


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

    The per-dispatch precondition gate is delegated to ``prepare_resume`` so
    this function composes a campaign-level ``ResumeDecision`` from one or
    more ``ResumePreflight`` results.

    Returns None if the state file is missing/corrupted. Returns a
    ResumeDecision with next_dispatch_name="" if all dispatches are
    complete or the campaign is halted.
    """
    from autoskillit.fleet.state import (  # noqa: PLC0415
        CampaignStateMutator,
        _clear_dispatch_for_retry,
        read_state,
    )

    # Pass 1: stale-RUNNING recovery + per-dispatch halt/reset for the FAILURE /
    # INTERRUPTED / REFUSED statuses. This pass mutates the file (closes the
    # mutator) before the composition pass re-reads state, so the compose pass
    # sees the post-reset status. prepare_resume is NOT used here because its
    # reset semantics for continue_on_failure=True differ from the campaign-level
    # requirements (it would reset FAILURE unconditionally on continue_on_failure
    # =True).
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            return None
        for d in m.state.dispatches:
            if d.status == DispatchStatus.RUNNING:
                resolve_stale_running(d, m, reason="stale_running_on_resume")
        for d in m.state.dispatches:
            if d.status in _RETRIABLE_NON_SUCCESS:
                if not continue_on_failure:
                    if reset_on_retry:
                        _clear_dispatch_for_retry(d)
                        m.mark_dirty()
                    else:
                        return ResumeDecision(
                            next_dispatch_name="",
                            completed_dispatches_block=FLEET_HALTED_SENTINEL,
                        )
                elif reset_on_retry and d.status != DispatchStatus.FAILURE:
                    # INTERRUPTED / REFUSED reset on reset_on_retry=True.
                    # FAILURE stays as FAILURE (continue_on_failure=True semantics).
                    _clear_dispatch_for_retry(d)
                    m.mark_dirty()

    # Re-open state via read_state for the composition pass; return None on fail-open.
    state = read_state(state_path)
    if state is None:
        return None

    completed_lines: list[str] = []
    next_name = ""
    is_resumable = False
    resumable_dispatched_session_id = ""
    resumable_dispatch_id = ""
    resumable_retry_reason = ""
    resumable_checkpoint: dict[str, Any] = {}

    for d in state.dispatches:
        # FAILURE / INTERRUPTED / REFUSED not reset by the pass-1 reset pass
        # are visible in the completed block (asymmetric semantics — see
        # TestContinueOnFailureDoesNotResetFailureDispatches and
        # TestRefusedDispatchVisibleInBlock). prepare_resume would reset
        # FAILURE unconditionally on continue_on_failure=True, so it is NOT
        # used for these statuses here.
        if d.status in _RETRIABLE_NON_SUCCESS:
            completed_lines.append(f"- {d.name}: {d.status}")
            continue
        # Delegate the canonical chokepoint for SUCCESS / PENDING / RESUMABLE /
        # SKIPPED / RELEASED. For SUCCESS it yields a short-circuit; for the
        # rest it returns a pass-through preflight.
        preflight = prepare_resume(
            state_path,
            d.name,
            continue_on_failure=continue_on_failure or reset_on_retry,
        )
        if preflight is None:
            continue
        if preflight.short_circuit is not None:
            # SUCCESS — render lowercase status via StrEnum.__format__.
            completed_lines.append(f"- {d.name}: {preflight.short_circuit.status}")
            continue
        # PASS_THROUGH — PENDING / RESUMABLE / SKIPPED / RELEASED.
        if d.status in _VISIBLE_IN_BLOCK_STATUSES:
            # SKIPPED or RELEASED.
            completed_lines.append(f"- {d.name}: {d.status}")
            continue
        if d.status == DispatchStatus.RESUMABLE and not next_name:
            # Defense-in-depth cap-conversion block (alongside L3 cap in
            # mark_dispatch_running). Mutates via a fresh CampaignStateMutator
            # to persist.
            timeout_count = _count_consecutive_resumable_timeouts(d.attempt_history)
            if timeout_count >= MAX_CONSECUTIVE_RESUME_ATTEMPTS:
                with CampaignStateMutator(state_path) as cap_m:
                    if cap_m.state is not None:
                        for x in cap_m.state.dispatches:
                            if x.name == d.name:
                                x.status = DispatchStatus.FAILURE
                                x.reason = (
                                    d.attempt_history[-1].get(
                                        "reason", FleetErrorCode.FLEET_L3_TIMEOUT
                                    )
                                    if d.attempt_history
                                    else FleetErrorCode.FLEET_L3_TIMEOUT
                                )
                                cap_m.mark_dirty()
                                break
                return ResumeDecision(
                    next_dispatch_name="",
                    completed_dispatches_block=FLEET_HALTED_SENTINEL,
                )
            next_name = d.name
            is_resumable = True
            resumable_dispatched_session_id = d.dispatched_session_id
            resumable_dispatch_id = d.dispatch_id
            resumable_retry_reason = d.retry_reason
            resumable_checkpoint = d.resume_checkpoint
            continue
        if not next_name:
            # PENDING or any other not-yet-resolved status.
            next_name = d.name

    completed_block = "\n".join(completed_lines) if completed_lines else ""

    return ResumeDecision(
        next_dispatch_name=next_name,
        completed_dispatches_block=completed_block,
        is_resumable=is_resumable,
        dispatched_session_id=resumable_dispatched_session_id,
        dispatch_id=resumable_dispatch_id,
        retry_reason=resumable_retry_reason,
        resume_checkpoint=resumable_checkpoint,
    )


def find_dispatch_for_issue(
    issue_url: str,
    campaign_state_paths: list[Path],
) -> DispatchRecord | None:
    """Search all known campaign states for a dispatch whose sidecar contains issue_url
    and whose labels have not been cleaned up.

    Pass 1: RUNNING dispatches (live session may own the label).
    Pass 2: terminal dispatches (FAILURE, INTERRUPTED) with labels_cleaned=False.
    Pass 3: PENDING dispatches with a stale attempt_history entry and no active session.

    RUNNING takes priority — a live session should not be preempted by an old dead dispatch.
    Returns the first matching DispatchRecord, else None. Reads are filesystem-only.
    Never raises.
    """
    from autoskillit.fleet.sidecar import read_sidecar_from_path  # noqa: PLC0415
    from autoskillit.fleet.state import read_state  # noqa: PLC0415

    terminal_match: DispatchRecord | None = None
    for state_path in campaign_state_paths:
        try:
            state = read_state(state_path)
        except Exception:
            logger.warning("Failed to read campaign state from %s", state_path, exc_info=True)
            continue
        if state is None:
            continue
        for d in state.dispatches:
            if d.issue_url and d.issue_url == issue_url:
                if d.status == DispatchStatus.RUNNING:
                    return d
                elif (
                    terminal_match is None
                    and d.status in TERMINAL_UNCLEANED_STATUSES
                    and not d.labels_cleaned
                ):
                    terminal_match = d
                continue

            if d.sidecar_path is None:
                continue
            if d.status == DispatchStatus.RUNNING:
                entries = read_sidecar_from_path(Path(d.sidecar_path)).entries
                if any(e.issue_url == issue_url for e in entries):
                    return d
            elif (
                terminal_match is None
                and d.status in TERMINAL_UNCLEANED_STATUSES
                and not d.labels_cleaned
            ):
                entries = read_sidecar_from_path(Path(d.sidecar_path)).entries
                if any(e.issue_url == issue_url for e in entries):
                    terminal_match = d

    if terminal_match is not None:
        return terminal_match

    for state_path in campaign_state_paths:
        try:
            state = read_state(state_path)
        except Exception:
            logger.warning("Failed to read campaign state from %s", state_path, exc_info=True)
            continue
        if state is None:
            continue
        for d in state.dispatches:
            if d.status != DispatchStatus.PENDING:
                continue
            if not d.issue_url or d.issue_url != issue_url:
                continue
            if d.labels_cleaned:
                continue
            if d.dispatched_session_id:
                continue
            if not d.attempt_history:
                continue
            last_attempt = d.attempt_history[-1]
            ended_at = last_attempt.get("ended_at", 0.0)
            if not ended_at or (time.time() - ended_at) > _PENDING_QUIET_PERIOD_SECONDS:
                return d
    return None


def _resume_backend_is_safe(*, session_id: str, source_backend: str, current_backend: str) -> bool:
    if not current_backend or source_backend == current_backend:
        return True
    event = "resume_backend_mismatch" if source_backend else "resume_backend_provenance_missing"
    logger.warning(
        event,
        session_id=session_id,
        source_backend=source_backend,
        current_backend=current_backend,
    )
    return False


def derive_orchestrator_resume_spec(
    state: CampaignState, *, current_backend: str = ""
) -> NamedResume | NoResume:
    """Derive the correct ResumeSpec for the L3 orchestrator from campaign state.

    Priority:
    1. state.orchestrator_session_id (if non-empty) → NamedResume
    2. Latest dispatch's caller_session_id (fallback) → NamedResume
    3. No session ID available → NoResume

    Backend-provenance guard:
        When ``current_backend`` is provided, resume is allowed only when the
        source DispatchRecord has the same ``caller_backend_name``. Missing or
        mismatched provenance returns NoResume. This prevents a backend-specific
        session ID from being passed to another backend's resume interface.
    """
    if state.orchestrator_session_id:
        if current_backend:
            if not state.dispatches:
                # Truly legacy state with no dispatches — fail closed when backend
                # provenance cannot be verified.
                logger.warning(
                    "resume_backend_provenance_missing",
                    session_id=state.orchestrator_session_id,
                    current_backend=current_backend,
                )
                return NoResume()
            source_record = next(
                (
                    dispatch
                    for dispatch in reversed(state.dispatches)
                    if dispatch.caller_session_id == state.orchestrator_session_id
                ),
                None,
            )
            if source_record is not None:
                source_backend = source_record.caller_backend_name
                if not _resume_backend_is_safe(
                    session_id=state.orchestrator_session_id,
                    source_backend=source_backend,
                    current_backend=current_backend,
                ):
                    return NoResume()
            # source_record is None: campaign has dispatches but none match the
            # orchestrator session id — accept the orchestrator session id as
            # authoritative since it was set via update_orchestrator_session_id
            # (not derived from a dispatch).
        return NamedResume(session_id=state.orchestrator_session_id)
    for d in reversed(state.dispatches):
        if d.caller_session_id:
            if not _resume_backend_is_safe(
                session_id=d.caller_session_id,
                source_backend=d.caller_backend_name,
                current_backend=current_backend,
            ):
                return NoResume()
            return NamedResume(session_id=d.caller_session_id)
    return NoResume()
