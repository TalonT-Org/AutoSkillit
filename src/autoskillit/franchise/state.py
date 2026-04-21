"""Campaign state file management — DispatchRecord, atomic writes, resume algorithm.

Provides the single-file state format for franchise campaign execution.
All writes use core.io.atomic_write for crash-safety (tmp + os.replace).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger, write_versioned_json

_log = get_logger(__name__)

_SCHEMA_VERSION = 2


class DispatchStatus(StrEnum):
    """Status of a single dispatch within a campaign."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    INTERRUPTED = "interrupted"
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
    l2_session_id: str = ""
    l2_session_log_dir: str = ""
    l2_pid: int = 0
    l2_starttime_ticks: int = 0
    l2_boot_id: str = ""
    reason: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    ended_at: float = 0.0

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


@dataclass(frozen=True)
class ResumeDecision:
    """Result of the resume algorithm."""

    next_dispatch_name: str
    completed_dispatches_block: str


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
        {DispatchStatus.SUCCESS, DispatchStatus.FAILURE, DispatchStatus.INTERRUPTED}
    ),
    # Terminal states: no further transitions permitted
    DispatchStatus.SUCCESS: frozenset(),
    DispatchStatus.FAILURE: frozenset(),
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
                l2_session_id=d.get("l2_session_id", ""),
                l2_session_log_dir=d.get("l2_session_log_dir", ""),
                l2_pid=d.get("l2_pid", 0),
                l2_starttime_ticks=d.get("l2_starttime_ticks", 0),
                l2_boot_id=d.get("l2_boot_id", ""),
                reason=d.get("reason", ""),
                token_usage=d.get("token_usage", {}),
                started_at=d.get("started_at", 0.0),
                ended_at=d.get("ended_at", 0.0),
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
        )
    except (KeyError, ValueError, TypeError) as exc:
        _log.warning("read_state: schema mismatch or corrupt payload in %s: %s", state_path, exc)
        return None


def _write_state(state_path: Path, state: CampaignState) -> None:
    """Internal: atomic write of full state to disk."""
    payload = {
        "campaign_id": state.campaign_id,
        "campaign_name": state.campaign_name,
        "manifest_path": state.manifest_path,
        "started_at": state.started_at,
        "dispatches": [d.to_dict() for d in state.dispatches],
    }
    write_versioned_json(state_path, payload, schema_version=state.schema_version)


def mark_dispatch_running(
    state_path: Path,
    dispatch_name: str,
    *,
    dispatch_id: str,
    l2_pid: int,
    starttime_ticks: int = 0,
    boot_id: str = "",
) -> None:
    """Atomically mark a dispatch as running with its dispatch_id and l2_pid."""
    state = read_state(state_path)
    if state is None:
        raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
    for d in state.dispatches:
        if d.name == dispatch_name:
            _validate_transition(d.status, DispatchStatus.RUNNING, d.name)
            d.status = DispatchStatus.RUNNING
            d.dispatch_id = dispatch_id
            d.l2_pid = l2_pid
            d.l2_starttime_ticks = starttime_ticks
            d.l2_boot_id = boot_id
            d.started_at = time.time()
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


_COMPLETED_STATUSES = frozenset({DispatchStatus.SUCCESS, DispatchStatus.SKIPPED})


def resume_campaign_from_state(
    state_path: Path,
    continue_on_failure: bool,
) -> ResumeDecision | None:
    """Determine the next dispatch for a resumed campaign.

    Algorithm:
      1. Read state.json; return None if absent or corrupted.
      2. Find first dispatch not in {success, skipped}.
      3. If running exists, mark it interrupted and continue from next.
      4. If failure exists and continue_on_failure=False, return None
         with reason franchise_halted_on_failure (encoded via a sentinel).
      5. Return ResumeDecision with next_dispatch_name and completed block.

    Returns None if the state file is missing/corrupted. Returns a
    ResumeDecision with next_dispatch_name="" if all dispatches are
    complete or the campaign is halted.
    """
    state = read_state(state_path)
    if state is None:
        return None

    # Phase 1: handle any stale "running" dispatch (crash recovery)
    for d in state.dispatches:
        if d.status == DispatchStatus.RUNNING:
            mark_dispatch_interrupted(state_path, d.name, reason="stale_running_on_resume")
            d.status = DispatchStatus.INTERRUPTED
            d.reason = "stale_running_on_resume"

    # Phase 2: check for failure halt
    for d in state.dispatches:
        if d.status == DispatchStatus.FAILURE and not continue_on_failure:
            return ResumeDecision(
                next_dispatch_name="",
                completed_dispatches_block="franchise_halted_on_failure",
            )

    # Phase 3: build completed dispatches block and find next
    completed_lines: list[str] = []
    next_name = ""
    for d in state.dispatches:
        if d.status in _COMPLETED_STATUSES:
            completed_lines.append(f"- {d.name}: {d.status}")
        elif not next_name:
            next_name = d.name

    completed_block = "\n".join(completed_lines) if completed_lines else ""

    return ResumeDecision(
        next_dispatch_name=next_name,
        completed_dispatches_block=completed_block,
    )
