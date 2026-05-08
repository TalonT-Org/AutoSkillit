"""Campaign state file management — DispatchRecord, atomic writes, resume algorithm.

Provides the single-file state format for fleet campaign execution.
All writes use core.io.atomic_write for crash-safety (tmp + os.replace).
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger, write_versioned_json
from autoskillit.fleet.state_gates import record_gate_outcome
from autoskillit.fleet.state_recovery import (
    crash_recover_dispatch,
    has_failed_dispatch,
    resume_campaign_from_state,
)
from autoskillit.fleet.state_types import (
    _ABANDON_KILL_REASONS,  # noqa: F401
    _ALLOWED_TRANSITIONS,  # noqa: F401
    _COMPLETED_STATUSES,  # noqa: F401
    _INFRASTRUCTURE_FAILURE_REASONS,  # noqa: F401
    _SCHEMA_VERSION,  # noqa: F401
    _VISIBLE_IN_BLOCK_STATUSES,  # noqa: F401
    FLEET_HALTED_SENTINEL,
    TERMINAL_DISPATCH_STATUSES,
    CampaignState,
    DispatchRecord,
    DispatchStatus,
    GateRecordResult,
    ResumeDecision,
    _resume_lock,
    _validate_transition,
)

__all__ = [
    # re-exported from state_gates
    "record_gate_outcome",
    # re-exported from state_recovery
    "crash_recover_dispatch",
    "has_failed_dispatch",
    "resume_campaign_from_state",
    # re-exported from state_types
    "FLEET_HALTED_SENTINEL",
    "TERMINAL_DISPATCH_STATUSES",
    "CampaignState",
    "DispatchRecord",
    "DispatchStatus",
    "GateRecordResult",
    "ResumeDecision",
    # local
    "write_initial_state",
    "read_state",
    "mark_dispatch_running",
    "mark_dispatch_interrupted",
    "mark_dispatch_resumable",
    "reset_failed_dispatch",
    "append_dispatch_record",
    "build_protected_campaign_ids",
    "write_captured_values",
    "read_all_campaign_captures",
    "update_orchestrator_session_id",
]

logger = get_logger(__name__)


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
        dispatches = [DispatchRecord.from_dict(d) for d in data["dispatches"]]
        return CampaignState(
            schema_version=data["schema_version"],
            campaign_id=data["campaign_id"],
            campaign_name=data["campaign_name"],
            manifest_path=data["manifest_path"],
            started_at=data["started_at"],
            dispatches=dispatches,
            captured_values=data.get("captured_values", {}),
            orchestrator_session_id=data.get("orchestrator_session_id") or "",
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
        "orchestrator_session_id": state.orchestrator_session_id,
    }
    write_versioned_json(state_path, payload, schema_version=state.schema_version)


def mark_dispatch_running(
    state_path: Path,
    dispatch_name: str,
    *,
    dispatch_id: str,
    dispatched_pid: int,
    starttime_ticks: int = 0,
    boot_id: str = "",
    sidecar_path: str | None = None,
) -> None:
    """Atomically mark a dispatch as running with its dispatch_id and dispatched_pid."""
    state = read_state(state_path)
    if state is None:
        raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
    for d in state.dispatches:
        if d.name == dispatch_name:
            _validate_transition(d.status, DispatchStatus.RUNNING, d.name)
            d.status = DispatchStatus.RUNNING
            d.dispatch_id = dispatch_id
            d.dispatched_pid = dispatched_pid
            d.dispatched_starttime_ticks = starttime_ticks
            d.dispatched_boot_id = boot_id
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


def update_orchestrator_session_id(state_path: Path, session_id: str) -> None:
    """Persist the L3 orchestrator's Claude Code session ID to campaign state.

    Thread-safe: uses fcntl.LOCK_EX on state.lock.
    """
    if not session_id:
        return
    with _resume_lock:
        lock_path = state_path.with_suffix(".lock")
        with open(lock_path, "ab") as _flock_handle:
            fcntl.flock(_flock_handle, fcntl.LOCK_EX)
            state = read_state(state_path)
            if state is None:
                logger.warning(
                    "update_orchestrator_session_id: state not found at %s",
                    state_path,
                )
                return
            state.orchestrator_session_id = session_id
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


def normalize_dispatch_token_usage(raw: dict[str, Any]) -> dict[str, int]:
    """Map raw Claude session token keys to canonical DispatchTokenUsage key set."""
    return {
        "input": int(raw.get("input_tokens", 0)),
        "output": int(raw.get("output_tokens", 0)),
        "cache_creation": int(raw.get("cache_creation_input_tokens", 0)),
        "cache_read": int(raw.get("cache_read_input_tokens", 0)),
    }
