"""Campaign state file management — DispatchRecord, atomic writes, resume algorithm.

Provides the single-file state format for fleet campaign execution.
All writes use core.io.atomic_write for crash-safety (tmp + os.replace).
"""

from __future__ import annotations

import fcntl
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from autoskillit.core import FleetErrorCode, get_logger, write_versioned_json

logger = get_logger(__name__)

_resume_lock = threading.Lock()

_SCHEMA_VERSION = 4

FLEET_HALTED_SENTINEL = "fleet_halted_on_failure"


class DispatchStatus(StrEnum):
    """Status of a single dispatch within a campaign."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    INTERRUPTED = "interrupted"
    RESUMABLE = "resumable"
    SKIPPED = "skipped"
    REFUSED = "refused"
    RELEASED = "released"


@dataclass
class DispatchRecord:
    """Runtime state of a single dispatch within a campaign.

    Mutable: status and metadata fields are updated as the dispatch progresses.
    """

    name: str
    status: DispatchStatus = DispatchStatus.PENDING
    dispatch_id: str = ""
    l3_session_id: str = ""
    l3_session_log_dir: str = ""
    l3_pid: int = 0
    l3_starttime_ticks: int = 0
    l3_boot_id: str = ""
    reason: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    ended_at: float = 0.0
    sidecar_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CampaignState:
    """Top-level campaign state file content."""

    schema_version: int
    campaign_id: str
    campaign_name: str
    manifest_path: str
    started_at: float
    dispatches: list[DispatchRecord] = field(default_factory=list)
    captured_values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumeDecision:
    """Result of the resume algorithm."""

    next_dispatch_name: str
    completed_dispatches_block: str
    is_resumable: bool = False
    l3_session_id: str = ""


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    DispatchStatus.PENDING: frozenset(
        {
            DispatchStatus.RUNNING,
            DispatchStatus.SUCCESS,
            DispatchStatus.FAILURE,
            DispatchStatus.SKIPPED,
            DispatchStatus.REFUSED,
            DispatchStatus.RELEASED,
        }
    ),
    DispatchStatus.RUNNING: frozenset(
        {
            DispatchStatus.SUCCESS,
            DispatchStatus.FAILURE,
            DispatchStatus.INTERRUPTED,
            DispatchStatus.RESUMABLE,
        }
    ),
    DispatchStatus.RESUMABLE: frozenset(
        {
            DispatchStatus.RUNNING,
            DispatchStatus.SUCCESS,
            DispatchStatus.FAILURE,
            DispatchStatus.INTERRUPTED,
        }
    ),
    # Retryable settled state: only explicit retry (→ PENDING) is allowed
    DispatchStatus.FAILURE: frozenset({DispatchStatus.PENDING}),
    # Terminal states: no further transitions permitted
    DispatchStatus.SUCCESS: frozenset(),
    DispatchStatus.INTERRUPTED: frozenset(),
    DispatchStatus.SKIPPED: frozenset(),
    DispatchStatus.REFUSED: frozenset(),
    DispatchStatus.RELEASED: frozenset(),
}


def _validate_transition(current: str, new: str, dispatch_name: str) -> None:
    """Raise ValueError if the status transition is not allowed."""
    allowed = _ALLOWED_TRANSITIONS.get(current)
    if allowed is not None and new not in allowed:
        msg = f"Invalid transition for dispatch '{dispatch_name}': {current!r} -> {new!r}"
        raise ValueError(msg)


def write_initial_state(
    state_path: Path,
    campaign_id: str,
    campaign_name: str,
    manifest_path: str,
    dispatches: list[DispatchRecord],
) -> None:
    """Create the campaign state file with all dispatches in pending status.

    Uses write_versioned_json for schema_version convention compliance.
    """
    payload = {
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "manifest_path": manifest_path,
        "started_at": time.time(),
        "dispatches": [d.to_dict() for d in dispatches],
    }
    write_versioned_json(state_path, payload, schema_version=_SCHEMA_VERSION)


_INFRASTRUCTURE_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
    }
)


def has_failed_dispatch(state_path: Path) -> bool:
    """Check whether any dispatch has a FAILURE status attributable to logic (not infrastructure).

    Infrastructure failures (e.g. fleet_l3_no_result_block) represent transient L3
    disconnections and do not halt the campaign. Logic failures (e.g. completed_clean
    with success=false) represent genuine task failures and do halt the campaign.

    Returns False when the file is missing or corrupted (fail-open).
    """
    if not state_path.exists():
        return False
    state = read_state(state_path)
    if state is None:
        return False
    return any(
        d.status == DispatchStatus.FAILURE and d.reason not in _INFRASTRUCTURE_FAILURE_REASONS
        for d in state.dispatches
    )


def _clear_dispatch_for_retry(d: DispatchRecord) -> None:
    _validate_transition(d.status, DispatchStatus.PENDING, d.name)
    d.status = DispatchStatus.PENDING
    d.reason = ""
    d.dispatch_id = ""
    d.l3_session_id = ""
    d.l3_session_log_dir = ""
    d.l3_pid = 0
    d.l3_starttime_ticks = 0
    d.l3_boot_id = ""
    d.token_usage = {}
    d.started_at = 0.0
    d.ended_at = 0.0
    d.sidecar_path = None


def reset_failed_dispatch(state_path: Path, dispatch_name: str) -> bool:
    """Reset a FAILURE dispatch to PENDING, clearing all execution metadata.

    Returns True if the dispatch was found in FAILURE state and reset,
    False if the dispatch was not found, not in FAILURE, or the state file
    is missing/corrupted. OSError raised by _write_state propagates to
    the caller — write failures are not silently converted to False.

    Thread-safe: uses _resume_lock + fcntl.LOCK_EX.
    """
    with _resume_lock:
        if not state_path.exists():
            return False
        lock_path = state_path.with_suffix(".lock")
        with open(lock_path, "wb") as _flock_handle:
            fcntl.flock(_flock_handle, fcntl.LOCK_EX)

            state = read_state(state_path)
            if state is None:
                return False

            for d in state.dispatches:
                if d.name == dispatch_name and d.status == DispatchStatus.FAILURE:
                    _clear_dispatch_for_retry(d)
                    _write_state(state_path, state)
                    return True

            return False


def read_state(state_path: Path) -> CampaignState | None:
    """Load campaign state from disk.

    Returns None on missing file, malformed JSON, or schema mismatch.
    Never raises.
    """
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        dispatches = [
            DispatchRecord(
                name=d["name"],
                status=DispatchStatus(d.get("status", DispatchStatus.PENDING)),
                dispatch_id=d.get("dispatch_id", ""),
                l3_session_id=d.get("l3_session_id") or d.get("l2_session_id", ""),
                l3_session_log_dir=d.get("l3_session_log_dir") or d.get("l2_session_log_dir", ""),
                l3_pid=d.get("l3_pid") or d.get("l2_pid", 0),
                l3_starttime_ticks=d.get("l3_starttime_ticks") or d.get("l2_starttime_ticks", 0),
                l3_boot_id=d.get("l3_boot_id") or d.get("l2_boot_id", ""),
                reason=d.get("reason", ""),
                token_usage=d.get("token_usage", {}),
                started_at=d.get("started_at", 0.0),
                ended_at=d.get("ended_at", 0.0),
                sidecar_path=d.get("sidecar_path"),
            )
            for d in data["dispatches"]
        ]
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


def mark_dispatch_running(
    state_path: Path,
    dispatch_name: str,
    *,
    dispatch_id: str,
    l3_pid: int,
    starttime_ticks: int = 0,
    boot_id: str = "",
    sidecar_path: str | None = None,
) -> None:
    """Atomically mark a dispatch as running with its dispatch_id and l3_pid."""
    state = read_state(state_path)
    if state is None:
        raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
    for d in state.dispatches:
        if d.name == dispatch_name:
            _validate_transition(d.status, DispatchStatus.RUNNING, d.name)
            d.status = DispatchStatus.RUNNING
            d.dispatch_id = dispatch_id
            d.l3_pid = l3_pid
            d.l3_starttime_ticks = starttime_ticks
            d.l3_boot_id = boot_id
            d.started_at = time.time()
            d.sidecar_path = sidecar_path
            break
    else:
        raise ValueError(f"Dispatch '{dispatch_name}' not found in state")
    _write_state(state_path, state)


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


@dataclass(frozen=True)
class GateRecordResult:
    """Result of a gate dispatch recording attempt."""

    success: bool
    dispatch_name: str
    status: str = ""
    error_code: str = ""
    error_message: str = ""


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
            else:
                state.dispatches.append(new_record)
                _write_state(state_path, state)

            return GateRecordResult(
                success=True,
                dispatch_name=dispatch_name,
                status=status.value,
            )


def append_dispatch_record(
    state_path: Path,
    record: DispatchRecord,
) -> None:
    """Atomically append or replace a dispatch record by name.

    If a dispatch with the same name exists, it is replaced in-place.
    Otherwise the record is appended to the end.
    """
    state = read_state(state_path)
    if state is None:
        raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
    for i, d in enumerate(state.dispatches):
        if d.name == record.name:
            _validate_transition(d.status, record.status, d.name)
            state.dispatches[i] = record
            _write_state(state_path, state)
            return
    state.dispatches.append(record)
    _write_state(state_path, state)


_COMPLETED_STATUSES = frozenset(
    {DispatchStatus.SUCCESS, DispatchStatus.SKIPPED, DispatchStatus.FAILURE}
)

_VISIBLE_IN_BLOCK_STATUSES = _COMPLETED_STATUSES | frozenset(
    {
        DispatchStatus.INTERRUPTED,
        DispatchStatus.REFUSED,
        DispatchStatus.RELEASED,
        DispatchStatus.RUNNING,
    }
)

TERMINAL_DISPATCH_STATUSES: frozenset[str] = frozenset(
    {
        DispatchStatus.SUCCESS,
        DispatchStatus.FAILURE,
        DispatchStatus.SKIPPED,
        DispatchStatus.RELEASED,
    }
)


def build_protected_campaign_ids(project_dir: Path) -> frozenset[str]:
    """Return campaign IDs with at least one non-terminal dispatch.

    Reads fleet state files from ``{project_dir}/.autoskillit/temp/dispatches/``.
    A campaign is protected if any of its dispatch records has a status that is NOT
    in the terminal set {success, failure, skipped, released}.
    Returns partially-accumulated results on unexpected errors rather than empty
    frozenset, so active campaigns processed before a failure are still protected.
    """
    protected: set[str] = set()
    try:
        dispatches_dir = project_dir / ".autoskillit" / "temp" / "dispatches"
        if not dispatches_dir.is_dir():
            return frozenset()
        for state_file in dispatches_dir.glob("*.json"):
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                cid = data.get("campaign_id", "")
                if not cid:
                    continue
                dispatches = data.get("dispatches", [])
                if not dispatches:
                    protected.add(cid)
                    continue
                for record in dispatches:
                    status = record.get("status", "")
                    if status not in TERMINAL_DISPATCH_STATUSES:
                        protected.add(cid)
                        break
            except (json.JSONDecodeError, OSError):
                continue
        return frozenset(protected)
    except Exception:
        logger.warning("campaign_ids_protection_error", exc_info=True)
        return frozenset(protected)


def write_captured_values(state_path: Path, captures: dict[str, str]) -> None:
    """Atomically merge new captures into an existing state file.

    Merges `captures` into the existing `captured_values` dict (new keys win).
    No-op if state file is missing or corrupted (logs a warning).
    """
    state = read_state(state_path)
    if state is None:
        logger.warning("write_captured_values: state not found at %s", state_path)
        return
    state.captured_values = {**state.captured_values, **captures}
    _write_state(state_path, state)


def read_all_campaign_captures(
    dispatches_dir: Path,
    campaign_id: str,
) -> dict[str, str]:
    """Accumulate captured_values from all SUCCESS dispatches for a campaign.

    Scans all *.json files in `dispatches_dir`. For each file matching
    `campaign_id` where every dispatch record has status SUCCESS, merges
    its `captured_values` into the result. Later files win on key collision.
    """
    result: dict[str, str] = {}
    if not dispatches_dir.is_dir():
        return result
    for path in dispatches_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("campaign_id") != campaign_id:
                continue
            caps = data.get("captured_values", {})
            if not caps:
                continue
            dispatches = data.get("dispatches", [])
            all_success = all(d.get("status") == DispatchStatus.SUCCESS for d in dispatches)
            if all_success and dispatches:
                result.update(caps)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("read_all_campaign_captures: skipping %s: %s", path, exc)
            continue
    return result


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

            # Phase 1: crash recovery — skip live sessions, recover stale ones
            for d in state.dispatches:
                if d.status == DispatchStatus.RUNNING:
                    if is_dispatch_session_alive(d):
                        continue
                    new_status = crash_recover_dispatch(state_path, d)
                    if new_status is not None:
                        d.status = new_status
                        d.reason = "stale_running_on_resume"

            # Phase 2: check for failure halt
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

            # Phase 3: build completed dispatches block and find next
            # RESUMABLE is selected before PENDING; RUNNING (alive) dispatches are skipped
            completed_lines: list[str] = []
            next_name = ""
            is_resumable = False
            resumable_l3_session_id = ""
            for d in state.dispatches:
                if d.status in _VISIBLE_IN_BLOCK_STATUSES:
                    completed_lines.append(f"- {d.name}: {d.status}")
                elif d.status == DispatchStatus.RESUMABLE and not next_name:
                    next_name = d.name
                    is_resumable = True
                    resumable_l3_session_id = d.l3_session_id
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
                l3_session_id=resumable_l3_session_id,
            )
