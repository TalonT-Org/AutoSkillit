"""Fleet dispatch-record / campaign-state / resume-decision / retry-mechanics authority.

Owns ``DispatchRecord``, ``CampaignState``, ``ResumeDecision``, the
schema-version / halted-sentinel constants, and the retry-clearing
mechanics (``_RETRY_IDENTITY_FIELDS``, ``_clear_dispatch_for_retry``).

Co-locating the retry-clear helper here (with the record it mutates)
breaks the cross-module cycle: ``state_records → state_transitions`` only.
Decomposed from ``state_types`` (#4856).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from autoskillit.core import (
    BackendAuthority,
    FleetErrorCode,
    ManagedHeadlessSessionLineageRef,
    ResolvedLaunchContract,
)
from autoskillit.fleet.state_transitions import (
    DispatchStatus,
    _validate_transition,
)

FLEET_STATE_SCHEMA_VERSION = 12

FLEET_HALTED_SENTINEL = "fleet_halted_on_failure"

_RETRY_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "campaign_id",
        "caller_session_id",
        "caller_backend_name",
        "attempt_history",
        "session_chain",
        "resume_count",
        "issue_url",
        "backend_name",
        "backend_authority",
        "launch_contract",
        "launch_contract_digest",
        "managed_lineage_ref",
        "_persisted_status",
    }
)


def _normalize_effect_provenance(raw: object) -> dict[str, Any]:
    """Normalize legacy persisted cleanup evidence to the fail-closed shape."""
    if not isinstance(raw, Mapping):
        raise TypeError("effect_provenance must be an object")
    local_cleanup_raw = raw.get("local_cleanup")
    if not isinstance(local_cleanup_raw, Mapping) or "observation_complete" in local_cleanup_raw:
        return dict(raw)
    local_cleanup = dict(local_cleanup_raw)
    local_cleanup["observation_complete"] = False
    local_cleanup["complete"] = False
    return {**raw, "local_cleanup": local_cleanup}


@dataclass
class DispatchRecord:
    """Runtime state of a single dispatch within a campaign.

    Mutable: status and metadata fields are updated as the dispatch progresses.
    """

    name: str
    status: DispatchStatus = DispatchStatus.PENDING
    _persisted_status: str = field(
        default="",
        repr=False,
        compare=False,
        kw_only=True,
        metadata={"persisted": False},
    )
    dispatch_id: str = ""
    campaign_id: str = ""
    caller_session_id: str = ""
    caller_backend_name: str = ""
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
    backend_name: str = ""
    backend_authority: BackendAuthority | None = None
    launch_contract: ResolvedLaunchContract | None = None
    launch_contract_digest: str = ""
    effect_provenance: dict[str, Any] = field(default_factory=dict)
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": (
                self._persisted_status or self.status
                if self.status == DispatchStatus.UNKNOWN
                else self.status
            ),
            "dispatch_id": self.dispatch_id,
            "campaign_id": self.campaign_id,
            "caller_session_id": self.caller_session_id,
            "caller_backend_name": self.caller_backend_name,
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
            "backend_name": self.backend_name,
            "backend_authority": (
                dict(self.backend_authority.to_payload())
                if self.backend_authority is not None
                else None
            ),
            "launch_contract": (
                json.loads(self.launch_contract.canonical_json)
                if self.launch_contract is not None
                else None
            ),
            "launch_contract_digest": self.launch_contract_digest,
            "effect_provenance": dict(self.effect_provenance),
            "managed_lineage_ref": (
                self.managed_lineage_ref.to_dict()
                if self.managed_lineage_ref is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DispatchRecord:
        status_raw = d.get("status", DispatchStatus.PENDING)
        if not isinstance(status_raw, str):
            raise TypeError(f"status must be str, got {type(status_raw).__name__!r}")
        status = DispatchStatus.from_persisted(status_raw)
        pid_raw = d.get("dispatched_pid")
        ticks_raw = d.get("dispatched_starttime_ticks")
        caller_backend_name = d.get("caller_backend_name", "")
        if not isinstance(caller_backend_name, str):
            raise TypeError(
                f"caller_backend_name must be str, got {type(caller_backend_name).__name__!r}"
            )
        managed_lineage_ref_raw = d.get("managed_lineage_ref")
        managed_lineage_ref = (
            ManagedHeadlessSessionLineageRef.from_dict(managed_lineage_ref_raw)
            if managed_lineage_ref_raw is not None
            else None
        )
        authority_raw = d.get("backend_authority")
        if authority_raw is not None and not isinstance(authority_raw, Mapping):
            raise TypeError("backend_authority must be an object")
        backend_authority = (
            BackendAuthority.from_payload(authority_raw) if authority_raw is not None else None
        )
        launch_raw = d.get("launch_contract")
        launch_digest = d.get("launch_contract_digest", "")
        if not isinstance(launch_digest, str):
            raise TypeError("launch_contract_digest must be str")
        if launch_raw is not None and not isinstance(launch_raw, Mapping):
            raise TypeError("launch_contract must be an object")
        if (launch_raw is None) != (not launch_digest):
            raise ValueError("launch contract payload and digest must be persisted together")
        launch_contract = (
            ResolvedLaunchContract.from_payload(
                launch_raw,
                expected_digest=launch_digest,
            )
            if launch_raw is not None
            else None
        )
        if launch_contract is not None:
            if backend_authority is None:
                raise ValueError("persisted launch contract requires typed backend authority")
            if backend_authority != launch_contract.backend_authority:
                raise ValueError("persisted fleet backend authority drifted from launch contract")
        return cls(
            name=d["name"],
            status=status,
            _persisted_status=status_raw if status == DispatchStatus.UNKNOWN else "",
            dispatch_id=d.get("dispatch_id", ""),
            campaign_id=d.get("campaign_id", ""),
            caller_session_id=d.get("caller_session_id", ""),
            caller_backend_name=caller_backend_name,
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
            backend_name=d.get("backend_name", ""),
            backend_authority=backend_authority,
            launch_contract=launch_contract,
            launch_contract_digest=launch_digest,
            effect_provenance=_normalize_effect_provenance(d.get("effect_provenance", {})),
            managed_lineage_ref=managed_lineage_ref,
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
        effect_provenance: dict[str, Any] | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
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
            effect_provenance=effect_provenance or {},
            managed_lineage_ref=managed_lineage_ref,
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
        effect_provenance: dict[str, Any] | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
    ) -> DispatchRecord:
        if diagnostic_message and diagnostic_message.strip():
            return cls.refused(
                name=name,
                error_code=error_code,
                diagnostic_message=diagnostic_message,
                dispatch_id=dispatch_id,
                caller_session_id=caller_session_id,
                effect_provenance=effect_provenance,
                managed_lineage_ref=managed_lineage_ref,
            )
        return cls(
            name=name,
            status=DispatchStatus.REFUSED,
            reason=str(error_code),
            dispatch_id=dispatch_id,
            caller_session_id=caller_session_id,
            effect_provenance=effect_provenance or {},
            managed_lineage_ref=managed_lineage_ref,
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
    opaque_dispatches: list[Any] = field(default_factory=list, kw_only=True)
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


def _clear_dispatch_for_retry(dispatch: DispatchRecord) -> None:
    """Snapshot and reset the non-identity fields of a retryable dispatch."""
    _validate_transition(dispatch.status, DispatchStatus.PENDING, dispatch.name)
    snapshot: dict[str, Any] = {}
    for field_def in dataclasses.fields(dispatch):
        if field_def.name in _RETRY_IDENTITY_FIELDS:
            continue
        value = getattr(dispatch, field_def.name)
        snapshot[field_def.name] = (
            str(value)
            if field_def.name == "status"
            else dict(value)
            if isinstance(value, dict)
            else value
        )
    dispatch.attempt_history.append(snapshot)
    for field_def in dataclasses.fields(dispatch):
        if field_def.name in _RETRY_IDENTITY_FIELDS:
            continue
        default = (
            field_def.default_factory()
            if field_def.default_factory is not dataclasses.MISSING
            else field_def.default
        )
        if default is dataclasses.MISSING:
            raise RuntimeError(
                f"Field {field_def.name!r} has no default; cannot reset it for retry"
            )
        setattr(dispatch, field_def.name, default)


__all__ = [
    "FLEET_HALTED_SENTINEL",
    "FLEET_STATE_SCHEMA_VERSION",
    "_RETRY_IDENTITY_FIELDS",
    "_clear_dispatch_for_retry",
    "CampaignState",
    "DispatchRecord",
    "ResumeDecision",
]
