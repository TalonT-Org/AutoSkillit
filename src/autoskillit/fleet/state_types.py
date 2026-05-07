"""Campaign state types and constants."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from autoskillit.core import FleetErrorCode, RetryReason

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
    campaign_id: str = ""
    caller_session_id: str = ""
    dispatched_session_id: str = ""
    dispatched_session_log_dir: str = ""
    dispatched_pid: int = 0
    dispatched_starttime_ticks: int = 0
    dispatched_boot_id: str = ""
    reason: str = ""
    kill_reason: str = ""
    infra_exit_category: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    ended_at: float = 0.0
    sidecar_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

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
            reason=d.get("reason", ""),
            kill_reason=d.get("kill_reason", ""),
            infra_exit_category=d.get("infra_exit_category", ""),
            token_usage=d.get("token_usage", {}),
            started_at=d.get("started_at", 0.0),
            ended_at=d.get("ended_at", 0.0),
            sidecar_path=d.get("sidecar_path"),
        )


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


_INFRASTRUCTURE_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
    }
)


@dataclass(frozen=True)
class GateRecordResult:
    """Result of a gate dispatch recording attempt."""

    success: bool
    dispatch_name: str
    status: str = ""
    error_code: str = ""
    error_message: str = ""


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


_ABANDON_KILL_REASONS: frozenset[str] = frozenset(
    {
        RetryReason.STALE,
        RetryReason.THINKING_STALL,
        RetryReason.PATH_CONTAMINATION,
        RetryReason.CLONE_CONTAMINATION,
    }
)
