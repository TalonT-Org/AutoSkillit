"""Campaign state types and constants."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from autoskillit.core import (
    BackendAuthority,
    FleetErrorCode,
    ManagedHeadlessSessionLineageRef,
    ProcessCleanupResult,
    ResolvedLaunchContract,
    RetryReason,
)

_resume_lock = threading.Lock()

FLEET_STATE_SCHEMA_VERSION = 11

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


class DispatchEffectName(StrEnum):
    """Stable vocabulary for retry-relevant fleet dispatch effects."""

    CAMPAIGN_PATH_CAPTURE = "campaign_path_capture"
    CALLER_IDENTITY = "caller_identity"
    DISPATCH_ALLOCATION = "dispatch_allocation"
    PRIOR_DISPATCH_BINDING = "prior_dispatch_binding"
    REQUESTED_RESUME_BINDING = "requested_resume_binding"
    EFFECTIVE_RESUME_BINDING = "effective_resume_binding"
    CHILD_DISCOVERY = "child_discovery"
    PROCESS_SPAWN = "process_spawn"
    COMMIT = "commit"
    CAMPAIGN_STATE_WRITE = "campaign_state_write"
    LOCAL_PROCESS_CLEANUP = "local_process_cleanup"
    STATE_CLEANUP = "state_cleanup"
    LABEL_CLEANUP = "label_cleanup"


class DispatchEffectPhase(StrEnum):
    """Lifecycle of one dispatch effect."""

    NOT_STARTED = "not_started"
    STARTED = "started"
    CONFIRMED = "confirmed"


class DispatchAggregatePhase(StrEnum):
    """Conservative aggregate of all retry-relevant dispatch effects."""

    NOT_STARTED = "not_started"
    STARTED = "started"
    COMMITTED = "committed"
    UNKNOWN = "unknown"


class DispatchRetryDisposition(StrEnum):
    """Safe caller action derived from effect provenance."""

    FRESH_DISPATCH_ALLOWED = "fresh_dispatch_allowed"
    RESUME_BY_IDENTITY = "resume_by_identity"
    RECONCILE_REQUIRED = "reconcile_required"


@dataclass(frozen=True, slots=True)
class DispatchEffectRecord:
    """Immutable checkpoint for one externally observable dispatch effect."""

    name: DispatchEffectName
    phase: DispatchEffectPhase
    effect_id: str
    retry_relevant: bool = True
    confirmation_receipt: str = ""
    known_downstream_identities: tuple[tuple[str, str], ...] = ()
    ambiguity: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "phase": self.phase.value,
            "effect_id": self.effect_id,
            "retry_relevant": self.retry_relevant,
            "confirmation_receipt": self.confirmation_receipt,
            "known_downstream_identities": dict(self.known_downstream_identities),
            "ambiguity": self.ambiguity,
        }


@dataclass(frozen=True, slots=True)
class DispatchEffectProvenance:
    """Immutable request snapshot used by domain, wire, and formatter layers."""

    operation_id: str
    effects: tuple[DispatchEffectRecord, ...] = ()
    cancel_requested: bool = False
    local_cleanup: ProcessCleanupResult | None = None
    state_cleanup_confirmed: bool = False
    labels_cleanup_confirmed: bool = False

    @property
    def aggregate_phase(self) -> DispatchAggregatePhase:
        relevant = tuple(effect for effect in self.effects if effect.retry_relevant)
        if any(
            effect.phase == DispatchEffectPhase.STARTED or effect.ambiguity for effect in relevant
        ):
            return DispatchAggregatePhase.UNKNOWN
        if any(
            effect.name == DispatchEffectName.COMMIT
            and effect.phase == DispatchEffectPhase.CONFIRMED
            for effect in relevant
        ):
            return DispatchAggregatePhase.COMMITTED
        if any(effect.phase == DispatchEffectPhase.CONFIRMED for effect in relevant):
            return DispatchAggregatePhase.STARTED
        return DispatchAggregatePhase.NOT_STARTED

    @property
    def retry_disposition(self) -> DispatchRetryDisposition:
        aggregate = self.aggregate_phase
        if aggregate == DispatchAggregatePhase.NOT_STARTED:
            return DispatchRetryDisposition.FRESH_DISPATCH_ALLOWED
        if aggregate == DispatchAggregatePhase.UNKNOWN:
            return DispatchRetryDisposition.RECONCILE_REQUIRED
        return DispatchRetryDisposition.RESUME_BY_IDENTITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "aggregate_phase": self.aggregate_phase.value,
            "retry_disposition": self.retry_disposition.value,
            "effects": [effect.to_dict() for effect in self.effects],
            "cancel_requested": self.cancel_requested,
            "local_cleanup": (
                self.local_cleanup.to_dict() if self.local_cleanup is not None else None
            ),
            "state_cleanup_confirmed": self.state_cleanup_confirmed,
            "labels_cleanup_confirmed": self.labels_cleanup_confirmed,
        }


class DispatchProvenanceTracker:
    """Request-scoped mutable journal that publishes immutable snapshots."""

    def __init__(self, operation_id: str | None = None) -> None:
        self.operation_id = operation_id or uuid.uuid4().hex
        self._effects: dict[DispatchEffectName, DispatchEffectRecord] = {}
        self._cancel_requested = False
        self._local_cleanup: ProcessCleanupResult | None = None
        self._state_cleanup_confirmed = False
        self._labels_cleanup_confirmed = False
        self._lock = threading.Lock()

    def _effect_id(self, name: DispatchEffectName) -> str:
        return f"{self.operation_id}:{name.value}"

    @staticmethod
    def _identities(values: Mapping[str, object] | None) -> tuple[tuple[str, str], ...]:
        if not values:
            return ()
        return tuple(sorted((key, str(value)) for key, value in values.items() if value != ""))

    def start(
        self,
        name: DispatchEffectName,
        *,
        retry_relevant: bool = True,
        identities: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            existing = self._effects.get(name)
            if existing is not None:
                return
            self._effects[name] = DispatchEffectRecord(
                name=name,
                phase=DispatchEffectPhase.STARTED,
                effect_id=self._effect_id(name),
                retry_relevant=retry_relevant,
                known_downstream_identities=self._identities(identities),
            )

    def confirm(
        self,
        name: DispatchEffectName,
        *,
        receipt: str,
        retry_relevant: bool = True,
        identities: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            existing = self._effects.get(name)
            if existing is None:
                raise ValueError(f"dispatch effect {name.value!r} was not started")
            if existing.retry_relevant is not retry_relevant:
                raise ValueError(
                    f"dispatch effect {name.value!r} changed retry relevance after start"
                )
            merged_identities = dict(existing.known_downstream_identities)
            if identities:
                merged_identities.update(
                    {key: str(value) for key, value in identities.items() if value != ""}
                )
            self._effects[name] = DispatchEffectRecord(
                name=name,
                phase=DispatchEffectPhase.CONFIRMED,
                effect_id=existing.effect_id,
                retry_relevant=existing.retry_relevant,
                confirmation_receipt=receipt,
                known_downstream_identities=self._identities(merged_identities),
            )

    def mark_ambiguous(
        self,
        name: DispatchEffectName,
        *,
        evidence: str,
        identities: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            existing = self._effects.get(name)
            merged_identities = dict(
                existing.known_downstream_identities if existing is not None else ()
            )
            if identities:
                merged_identities.update(
                    {key: str(value) for key, value in identities.items() if value != ""}
                )
            self._effects[name] = DispatchEffectRecord(
                name=name,
                phase=DispatchEffectPhase.STARTED,
                effect_id=existing.effect_id if existing is not None else self._effect_id(name),
                retry_relevant=(existing.retry_relevant if existing is not None else True),
                known_downstream_identities=self._identities(merged_identities),
                ambiguity=evidence,
            )

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True

    def record_local_cleanup(self, result: ProcessCleanupResult) -> None:
        with self._lock:
            self._local_cleanup = result

    def record_state_cleanup(self, *, confirmed: bool) -> None:
        with self._lock:
            self._state_cleanup_confirmed = confirmed

    def record_labels_cleanup(self, *, confirmed: bool) -> None:
        with self._lock:
            self._labels_cleanup_confirmed = confirmed

    def snapshot(self) -> DispatchEffectProvenance:
        with self._lock:
            return DispatchEffectProvenance(
                operation_id=self.operation_id,
                effects=tuple(
                    self._effects[name]
                    for name in sorted(self._effects, key=lambda item: item.value)
                ),
                cancel_requested=self._cancel_requested,
                local_cleanup=self._local_cleanup,
                state_cleanup_confirmed=self._state_cleanup_confirmed,
                labels_cleanup_confirmed=self._labels_cleanup_confirmed,
            )


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
            "status": self.status,
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
            status=DispatchStatus(d.get("status", DispatchStatus.PENDING)),
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
            effect_provenance=d.get("effect_provenance", {}),
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
    effect_provenance: DispatchEffectProvenance = field(kw_only=True)
    details: dict[str, Any] | None = None
    dispatch_id: str = ""

    def to_envelope(self) -> str:
        d: dict[str, Any] = {
            "kind": "rejected",
            "success": False,
            "error": self.error_code,
            "user_visible_message": self.message,
            "details": self.details,
            "effect_provenance": self.effect_provenance.to_dict(),
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
    diagnostic_message: str = ""
    effect_provenance: DispatchEffectProvenance = field(kw_only=True)
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
        reason_text = str(self.reason)
        d: dict[str, Any] = {
            "kind": "completed",
            "success": self.success,
            "dispatch_status": self.dispatch_status.value,
            "dispatch_id": self.dispatch_id,
            "dispatched_session_id": self.dispatched_session_id,
            "reason": self.reason,
            "effect_provenance": self.effect_provenance.to_dict(),
            "token_usage": self.token_usage,
            "l3_payload": self.l3_payload,
            "l3_parse_source": self.l3_parse_source,
            "lifespan_started": self.lifespan_started,
            "stderr": self.stderr,
            "elapsed_seconds": self.elapsed_seconds,
        }
        if not self.success:
            d["error"] = reason_text or FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH.value
            d["user_visible_message"] = self.diagnostic_message or reason_text
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
