"""Campaign state types and constants."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from autoskillit.core import FleetErrorCode, RetryReason

_resume_lock = threading.Lock()

FLEET_STATE_SCHEMA_VERSION = 5

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
    campaign_id: str = ""
    caller_session_id: str = ""
    dispatched_session_id: str = ""
    dispatched_session_log_dir: str = ""
    dispatched_pid: int = 0
    dispatched_starttime_ticks: int = 0
    dispatched_boot_id: str = ""
    dispatched_create_time: float = 0.0
    reason: str = ""
    kill_reason: str = ""
    infra_exit_category: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    ended_at: float = 0.0
    sidecar_path: str | None = None
    attempt_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "dispatch_id": self.dispatch_id,
            "campaign_id": self.campaign_id,
            "caller_session_id": self.caller_session_id,
            "dispatched_session_id": self.dispatched_session_id,
            "dispatched_session_log_dir": self.dispatched_session_log_dir,
            "dispatched_pid": self.dispatched_pid,
            "dispatched_starttime_ticks": self.dispatched_starttime_ticks,
            "dispatched_boot_id": self.dispatched_boot_id,
            "dispatched_create_time": self.dispatched_create_time,
            "reason": self.reason,
            "kill_reason": self.kill_reason,
            "infra_exit_category": self.infra_exit_category,
            "token_usage": dict(self.token_usage),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "sidecar_path": self.sidecar_path,
            "attempt_history": list(self.attempt_history),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DispatchRecord:
        pid_raw = d.get("dispatched_pid")
        ticks_raw = d.get("dispatched_starttime_ticks")
        return cls(
            name=d["name"],
            status=DispatchStatus(d.get("status", DispatchStatus.PENDING)),
            dispatch_id=d.get("dispatch_id", ""),
            campaign_id=d.get("campaign_id", ""),
            caller_session_id=d.get("caller_session_id", ""),
            dispatched_session_id=(
                d.get("dispatched_session_id")
                or d.get("l3_session_id")
                or d.get("l2_session_id", "")
            ),
            dispatched_session_log_dir=(
                d.get("dispatched_session_log_dir")
                or d.get("l3_session_log_dir")
                or d.get("l2_session_log_dir", "")
            ),
            dispatched_pid=(
                pid_raw
                if pid_raw is not None
                else (l3_pid if (l3_pid := d.get("l3_pid")) is not None else d.get("l2_pid", 0))
            ),
            dispatched_starttime_ticks=(
                ticks_raw
                if ticks_raw is not None
                else (
                    l3_ticks
                    if (l3_ticks := d.get("l3_starttime_ticks")) is not None
                    else d.get("l2_starttime_ticks", 0)
                )
            ),
            dispatched_boot_id=(
                d.get("dispatched_boot_id") or d.get("l3_boot_id") or d.get("l2_boot_id", "")
            ),
            dispatched_create_time=d.get("dispatched_create_time", 0.0),
            reason=d.get("reason", ""),
            kill_reason=d.get("kill_reason", ""),
            infra_exit_category=d.get("infra_exit_category", ""),
            token_usage=d.get("token_usage", {}),
            started_at=d.get("started_at", 0.0),
            ended_at=d.get("ended_at", 0.0),
            sidecar_path=d.get("sidecar_path"),
            attempt_history=d.get("attempt_history", []),
        )


@dataclass
class CampaignState:
    """Top-level campaign state file content."""

    campaign_id: str
    campaign_name: str
    manifest_path: str
    started_at: float
    schema_version: int = FLEET_STATE_SCHEMA_VERSION
    dispatches: list[DispatchRecord] = field(default_factory=list)
    captured_values: dict[str, str] = field(default_factory=dict)
    orchestrator_session_id: str = ""
    ended_at: float = 0.0
    recipe_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    """Result of the resume algorithm."""

    next_dispatch_name: str
    completed_dispatches_block: str
    is_resumable: bool = False
    dispatched_session_id: str = ""
    kill_reason: str = ""


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
    DispatchStatus.FAILURE: frozenset({DispatchStatus.PENDING}),
    DispatchStatus.SUCCESS: frozenset(),
    DispatchStatus.INTERRUPTED: frozenset({DispatchStatus.PENDING}),
    DispatchStatus.SKIPPED: frozenset(),
    DispatchStatus.REFUSED: frozenset({DispatchStatus.PENDING}),
    DispatchStatus.RELEASED: frozenset(),
}

for _ds in DispatchStatus:
    if _ds not in _ALLOWED_TRANSITIONS:
        raise AssertionError(f"DispatchStatus.{_ds.name} missing from _ALLOWED_TRANSITIONS")
del _ds


def _validate_transition(current: str, new: str, dispatch_name: str) -> None:
    """Raise ValueError if the status transition is not allowed."""
    allowed = _ALLOWED_TRANSITIONS.get(current)
    if allowed is not None and new not in allowed:
        msg = f"Invalid transition for dispatch '{dispatch_name}': {current!r} -> {new!r}"
        raise ValueError(msg)


_INFRASTRUCTURE_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
        FleetErrorCode.FLEET_QUOTA_EXHAUSTED,
    }
)


@dataclass(frozen=True, slots=True)
class GateRecordResult:
    """Result of a gate dispatch recording attempt."""

    success: bool
    dispatch_name: str
    status: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class DispatchRejected:
    """Pre-validation or infrastructure rejection — no subprocess was launched."""

    error_code: FleetErrorCode
    message: str
    details: dict[str, Any] | None = None
    dispatch_id: str = ""

    def to_envelope(self) -> str:
        d: dict[str, Any] = {
            "success": False,
            "error": self.error_code,
            "user_visible_message": self.message,
            "details": self.details,
        }
        if self.dispatch_id:
            d["dispatch_id"] = self.dispatch_id
        return json.dumps(d)


@dataclass(frozen=True, slots=True)
class DispatchCompleted:
    """Dispatch that reached subprocess phase — may have succeeded or failed."""

    success: bool
    dispatch_status: DispatchStatus
    dispatch_id: str
    dispatched_session_id: str
    reason: str
    token_usage: dict[str, Any] = field(default_factory=dict)
    l3_payload: dict[str, Any] | None = None
    l3_parse_source: str = ""
    lifespan_started: bool = False
    l3_raw_body: str | None = None
    l3_parse_error: str | None = None
    resume_checkpoint: dict[str, Any] | None = None
    stderr: str = ""

    def to_envelope(self) -> str:
        d: dict[str, Any] = {
            "success": self.success,
            "dispatch_status": self.dispatch_status.value,
            "dispatch_id": self.dispatch_id,
            "dispatched_session_id": self.dispatched_session_id,
            "reason": self.reason,
            "token_usage": self.token_usage,
            "l3_payload": self.l3_payload,
            "l3_parse_source": self.l3_parse_source,
            "lifespan_started": self.lifespan_started,
            "stderr": self.stderr,
        }
        if self.l3_raw_body is not None:
            d["l3_raw_body"] = self.l3_raw_body
        if self.l3_parse_error is not None:
            d["l3_parse_error"] = self.l3_parse_error
        if self.resume_checkpoint is not None:
            d["resume_checkpoint"] = self.resume_checkpoint
        return json.dumps(d)


DispatchOutcome = DispatchCompleted | DispatchRejected


_COMPLETED_STATUSES = frozenset(
    {DispatchStatus.SUCCESS, DispatchStatus.SKIPPED, DispatchStatus.FAILURE}
)

_VISIBLE_IN_BLOCK_STATUSES = _COMPLETED_STATUSES | frozenset(
    {
        DispatchStatus.RELEASED,
        DispatchStatus.RUNNING,
        DispatchStatus.INTERRUPTED,
        DispatchStatus.REFUSED,
    }
)

TERMINAL_DISPATCH_STATUSES: frozenset[str] = frozenset(
    status for status, transitions in _ALLOWED_TRANSITIONS.items() if not transitions
)


_ABANDON_REASONS: frozenset[str] = frozenset(
    {
        RetryReason.STALE,
        RetryReason.THINKING_STALL,
        RetryReason.PATH_CONTAMINATION,
        RetryReason.CLONE_CONTAMINATION,
    }
)
