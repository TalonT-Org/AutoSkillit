"""Campaign state types and constants."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from autoskillit.core import FleetErrorCode, RetryReason

_resume_lock = threading.Lock()

FLEET_STATE_SCHEMA_VERSION = 8

FLEET_HALTED_SENTINEL = "fleet_halted_on_failure"

_RETRY_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "campaign_id",
        "caller_session_id",
        "attempt_history",
        "session_chain",
        "resume_count",
    }
)


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


class ErrorCodeCategory(StrEnum):
    """Category for an error code — determines whether it halts a campaign."""

    INFRASTRUCTURE = "infrastructure"
    LOGIC = "logic"


_ERROR_CODE_CATEGORIES: dict[str, ErrorCodeCategory] = {
    FleetErrorCode.FLEET_L3_TIMEOUT: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_L3_PARSE_FAILED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_QUOTA_EXHAUSTED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_CLEANUP_FAILED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_ACQUIRE_TIMEOUT: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_PARALLEL_REFUSED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_HARD_REFUSAL_HEADLESS: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_MANIFEST_MISSING: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_MANIFEST_CORRUPTED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_LOCK_NOT_INITIALIZED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_RECIPE_INVALID: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_PROCESS_STALE: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_FEATURE_DISABLED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_DISPATCH_SKIPPED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_GATE_ALREADY_RECORDED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_GATE_NO_CAMPAIGN: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_GATE_UNKNOWN_DISPATCH: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_BUDGET_EXCEEDED: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_RESUME_SESSION_MISSING: ErrorCodeCategory.INFRASTRUCTURE,
    FleetErrorCode.FLEET_UNKNOWN_INGREDIENT: ErrorCodeCategory.LOGIC,
    FleetErrorCode.FLEET_MISSING_INGREDIENT: ErrorCodeCategory.LOGIC,
    FleetErrorCode.FLEET_CAMPAIGN_HALTED: ErrorCodeCategory.LOGIC,
    FleetErrorCode.FLEET_RECIPE_NOT_FOUND: ErrorCodeCategory.LOGIC,
    FleetErrorCode.FLEET_INVALID_RECIPE_KIND: ErrorCodeCategory.LOGIC,
}


def get_error_category(code: str) -> ErrorCodeCategory:
    """Return the category for an error code. Unrecognized codes default to LOGIC."""
    return _ERROR_CODE_CATEGORIES.get(code, ErrorCodeCategory.LOGIC)


# Derived from metadata for exhaustiveness
_INFRASTRUCTURE_FAILURE_REASONS: frozenset[str] = frozenset(
    code for code, cat in _ERROR_CODE_CATEGORIES.items() if cat == ErrorCodeCategory.INFRASTRUCTURE
)


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
    session_chain: list[str] = field(default_factory=list)
    dispatched_session_log_dir: str = ""
    dispatched_pid: int = 0
    dispatched_starttime_ticks: int = 0
    dispatched_boot_id: str = ""
    dispatched_create_time: float = 0.0
    identity_degraded: bool = False
    reason: str = ""
    diagnostic_message: str = ""
    retry_reason: str = ""
    infra_exit_category: str = ""
    reaper_reason: str = ""
    reaper_dispatch_id: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    ended_at: float = 0.0
    sidecar_path: str | None = None
    labels_cleaned: bool = False
    issue_url: str = ""
    branch_name: str = ""
    attempt_history: list[dict[str, Any]] = field(default_factory=list)
    resume_checkpoint: dict[str, Any] = field(default_factory=dict)
    wait_seconds: float | None = None
    resets_at: str = ""
    resume_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "dispatch_id": self.dispatch_id,
            "campaign_id": self.campaign_id,
            "caller_session_id": self.caller_session_id,
            "dispatched_session_id": self.dispatched_session_id,
            "session_chain": list(self.session_chain),
            "dispatched_session_log_dir": self.dispatched_session_log_dir,
            "dispatched_pid": self.dispatched_pid,
            "dispatched_starttime_ticks": self.dispatched_starttime_ticks,
            "dispatched_boot_id": self.dispatched_boot_id,
            "dispatched_create_time": self.dispatched_create_time,
            "identity_degraded": self.identity_degraded,
            "reason": self.reason,
            "diagnostic_message": self.diagnostic_message,
            "retry_reason": self.retry_reason,
            "infra_exit_category": self.infra_exit_category,
            "reaper_reason": self.reaper_reason,
            "reaper_dispatch_id": self.reaper_dispatch_id,
            "token_usage": dict(self.token_usage),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "sidecar_path": self.sidecar_path,
            "labels_cleaned": self.labels_cleaned,
            "issue_url": self.issue_url,
            "branch_name": self.branch_name,
            "attempt_history": list(self.attempt_history),
            "resume_checkpoint": dict(self.resume_checkpoint),
            "wait_seconds": self.wait_seconds,
            "resets_at": self.resets_at,
            "resume_count": self.resume_count,
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
            session_chain=d.get("session_chain", []),
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
            identity_degraded=d.get("identity_degraded", False),
            reason=d.get("reason", ""),
            diagnostic_message=d.get("diagnostic_message", ""),
            retry_reason=d["retry_reason"] if "retry_reason" in d else d.get("kill_reason", ""),
            infra_exit_category=d.get("infra_exit_category", ""),
            reaper_reason=d.get("reaper_reason", ""),
            reaper_dispatch_id=d.get("reaper_dispatch_id", ""),
            token_usage=d.get("token_usage", {}),
            started_at=d.get("started_at", 0.0),
            ended_at=d.get("ended_at", 0.0),
            sidecar_path=d.get("sidecar_path"),
            labels_cleaned=d.get("labels_cleaned", False),
            issue_url=d.get("issue_url", ""),
            branch_name=d.get("branch_name", ""),
            attempt_history=d.get("attempt_history", []),
            resume_checkpoint=d.get("resume_checkpoint", {}),
            wait_seconds=d.get("wait_seconds"),
            resets_at=d.get("resets_at", ""),
            resume_count=d.get("resume_count", 0),
        )

    @classmethod
    def refused(
        cls,
        *,
        name: str,
        error_code: FleetErrorCode | str,
        diagnostic_message: str,
        dispatch_id: str = "",
        caller_session_id: str = "",
    ) -> DispatchRecord:
        if not diagnostic_message or not diagnostic_message.strip():
            raise ValueError("diagnostic_message is required for refused records")
        return cls(
            name=name,
            status=DispatchStatus.REFUSED,
            reason=str(error_code),
            diagnostic_message=diagnostic_message,
            dispatch_id=dispatch_id,
            caller_session_id=caller_session_id,
        )

    @classmethod
    def for_refusal(
        cls,
        *,
        name: str,
        error_code: FleetErrorCode | str,
        diagnostic_message: str = "",
        dispatch_id: str = "",
        caller_session_id: str = "",
    ) -> DispatchRecord:
        if diagnostic_message and diagnostic_message.strip():
            return cls.refused(
                name=name,
                error_code=error_code,
                diagnostic_message=diagnostic_message,
                dispatch_id=dispatch_id,
                caller_session_id=caller_session_id,
            )
        return cls(
            name=name,
            status=DispatchStatus.REFUSED,
            reason=str(error_code),
            dispatch_id=dispatch_id,
            caller_session_id=caller_session_id,
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
    dispatch_id: str = ""
    retry_reason: str = ""
    resume_checkpoint: dict[str, Any] = field(default_factory=dict)


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
    resume_checkpoint: dict[str, Any] = field(default_factory=dict)
    stderr: str = ""
    elapsed_seconds: float = 0.0
    health_report: dict[str, Any] | None = None

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
            "elapsed_seconds": self.elapsed_seconds,
        }
        if self.l3_raw_body is not None:
            d["l3_raw_body"] = self.l3_raw_body
        if self.l3_parse_error is not None:
            d["l3_parse_error"] = self.l3_parse_error
        if self.resume_checkpoint:
            d["resume_checkpoint"] = self.resume_checkpoint
        if self.health_report is not None:
            d["health_report"] = self.health_report
        return json.dumps(d)


DispatchOutcome = DispatchCompleted | DispatchRejected


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Result of execute_dispatch: the outcome plus the per-dispatch state path.

    The per_dispatch_state_path is None when the dispatch never reached
    _run_dispatch (e.g., quota check failure, lock timeout).
    """

    outcome: DispatchOutcome
    per_dispatch_state_path: Path | None = None


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

TERMINAL_UNCLEANED_STATUSES: frozenset[DispatchStatus] = frozenset(
    {DispatchStatus.FAILURE, DispatchStatus.INTERRUPTED}
)

_ABANDON_REASONS: frozenset[str] = frozenset(
    {
        RetryReason.STALE,
        RetryReason.THINKING_STALL,
        RetryReason.PATH_CONTAMINATION,
        RetryReason.CLONE_CONTAMINATION,
        RetryReason.IDLE_STALL,
        RetryReason.CANCELLED,  # transport teardown — session was never started or was torn down
    }
)
