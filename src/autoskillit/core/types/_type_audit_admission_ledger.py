"""Frozen contracts for the parent-owned durable audit admission ledger.

The ledger is the single mutable authority for audit installations,
reservations, attempts, trusted heads, per-step preflight projections, and
disposition publications.  It is the durable-storage boundary that the
value objects in :mod:`_type_audit_admission` flow through. Recipe execution,
preflight, materialization, and disposition publication consume this one
authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from ._type_audit_admission import (
    AuditAttemptId,
    AuditIdentityReservation,
    AuditOutcome,
    AuditPreparedEffect,
    AuditSlotKey,
    InstallationVersion,
    RecipeExecutionId,
    ReservationDecision,
    _require_absolute_path,
    _require_digest,
    _require_nonempty,
    _require_optional_digest,
)
from ._type_audit_cycle import ArtifactRef, AuditCycleHead

__all__ = [
    "AuditAdmissionLedger",
    "AuditAdmissionRecoveryResult",
    "AuditAdmissionStorageError",
    "AuditAdmissionStorageFailureReason",
    "AuditAdmissionStorageHealthStatus",
    "AuditAdmissionStoreAuthority",
    "AuditAdmissionStoreHealth",
    "AuditDispositionCommitOutcome",
    "AuditDispositionCommitRequest",
    "AuditFinalCommitOutcome",
    "AuditFinalCommitRequest",
    "AuditPreflightProjection",
    "AuditPrepareOutcome",
    "AuditPrepareRequest",
    "AuditReservationOutcome",
    "AuditReservationRequest",
]


class AuditAdmissionStorageHealthStatus(StrEnum):
    """Ledger-instance storage health, independent of any single attempt."""

    UNRECOVERED = "UNRECOVERED"
    HEALTHY = "HEALTHY"
    FAIL_CLOSED = "FAIL_CLOSED"


class AuditAdmissionStorageFailureReason(StrEnum):
    """Bounded reasons for sticky audit-admission storage failure."""

    CONFIGURATION = "CONFIGURATION"
    IO = "IO"
    SECURITY_IDENTITY = "SECURITY_IDENTITY"
    INTEGRITY = "INTEGRITY"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    REPLAY_MISMATCH = "REPLAY_MISMATCH"


class AuditAdmissionStorageError(RuntimeError):
    """Raised for fail-closed storage health or an unrecovered ledger instance."""

    def __init__(
        self,
        reason: AuditAdmissionStorageFailureReason,
        reason_code: str,
    ) -> None:
        super().__init__(f"{reason.value}: {reason_code}")
        self.reason = reason
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class AuditAdmissionStoreAuthority:
    """Process-local authority for opening one audit-admission store."""

    database_path: Path
    expected_owner_id: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.database_path, Path)
            or not self.database_path.is_absolute()
            or not self.database_path.name
        ):
            raise ValueError("invalid_audit_admission_store_path")
        if (
            not isinstance(self.expected_owner_id, int)
            or isinstance(self.expected_owner_id, bool)
            or self.expected_owner_id < 0
        ):
            raise ValueError("invalid_audit_admission_store_owner")


@dataclass(frozen=True, slots=True)
class AuditAdmissionStoreHealth:
    status: AuditAdmissionStorageHealthStatus
    failure_reason: AuditAdmissionStorageFailureReason | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AuditAdmissionStorageHealthStatus):
            raise ValueError("AuditAdmissionStoreHealth.status has the wrong type")
        if self.status is AuditAdmissionStorageHealthStatus.FAIL_CLOSED:
            if self.failure_reason is None:
                raise ValueError("FAIL_CLOSED health requires failure_reason")
            _require_nonempty("AuditAdmissionStoreHealth.reason_code", self.reason_code)
        elif self.failure_reason is not None or self.reason_code is not None:
            raise ValueError("only FAIL_CLOSED health may carry a failure reason")


@dataclass(frozen=True, slots=True)
class AuditAdmissionRecoveryResult:
    store_health: AuditAdmissionStoreHealth
    recovered_installations: tuple[RecipeExecutionId, ...]
    recovered_attempts: tuple[AuditAttemptId, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.store_health, AuditAdmissionStoreHealth):
            raise ValueError("AuditAdmissionRecoveryResult.store_health has the wrong type")
        if self.store_health.status is not AuditAdmissionStorageHealthStatus.HEALTHY and (
            self.recovered_installations or self.recovered_attempts
        ):
            raise ValueError("only a HEALTHY recovery may report recovered records")


@dataclass(frozen=True, slots=True)
class AuditReservationRequest:
    """The attested runtime facts required to reserve one audit slot."""

    recipe_execution_id: RecipeExecutionId
    installation_version: InstallationVersion
    step_name: str
    invocation_template_digest: str
    slot_intent_digest: str
    runtime_binding_digest: str
    audited_plan_refs: tuple[ArtifactRef, ...]
    cycle_id: str
    scope_id: str
    part_id: str
    allowed_root: Path
    parent_authority_digest: str | None = None
    retry_after_audit_attempt_id: AuditAttemptId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.recipe_execution_id, RecipeExecutionId):
            raise ValueError("AuditReservationRequest.recipe_execution_id has the wrong type")
        if not isinstance(self.installation_version, InstallationVersion):
            raise ValueError("AuditReservationRequest.installation_version has the wrong type")
        _require_nonempty("AuditReservationRequest.step_name", self.step_name)
        _require_digest(
            "AuditReservationRequest.invocation_template_digest",
            self.invocation_template_digest,
        )
        _require_digest(
            "AuditReservationRequest.slot_intent_digest",
            self.slot_intent_digest,
        )
        _require_digest(
            "AuditReservationRequest.runtime_binding_digest",
            self.runtime_binding_digest,
        )
        refs = tuple(self.audited_plan_refs)
        if not refs or any(not isinstance(ref, ArtifactRef) for ref in refs):
            raise ValueError("AuditReservationRequest.audited_plan_refs must be non-empty")
        object.__setattr__(self, "audited_plan_refs", refs)
        for name in ("cycle_id", "scope_id", "part_id"):
            _require_nonempty(f"AuditReservationRequest.{name}", getattr(self, name))
        _require_absolute_path("AuditReservationRequest.allowed_root", self.allowed_root)
        _require_optional_digest(
            "AuditReservationRequest.parent_authority_digest",
            self.parent_authority_digest,
        )
        if self.retry_after_audit_attempt_id is not None and not isinstance(
            self.retry_after_audit_attempt_id, AuditAttemptId
        ):
            raise ValueError(
                "AuditReservationRequest.retry_after_audit_attempt_id has the wrong type"
            )


@dataclass(frozen=True, slots=True)
class AuditReservationOutcome:
    decision: ReservationDecision
    slot_key: AuditSlotKey
    attempt_id: AuditAttemptId
    reservation: AuditIdentityReservation | None = None
    reservation_handle: str | None = None
    replay_outcome: AuditOutcome | None = None
    conflict_detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ReservationDecision):
            raise ValueError("AuditReservationOutcome.decision has the wrong type")
        if not isinstance(self.slot_key, AuditSlotKey):
            raise ValueError("AuditReservationOutcome.slot_key has the wrong type")
        if not isinstance(self.attempt_id, AuditAttemptId):
            raise ValueError("AuditReservationOutcome.attempt_id has the wrong type")
        dispatchable = {
            ReservationDecision.DISPATCH_NEW,
            ReservationDecision.REDISPATCH_OPEN,
        }
        resumable = {
            ReservationDecision.RESUME_PREPARED,
            ReservationDecision.PUBLISHED_PENDING_FINALIZATION,
        }
        if self.decision in dispatchable:
            if self.reservation is None or not self.reservation_handle:
                raise ValueError("a dispatch decision requires a reservation and handle")
            if self.replay_outcome is not None or self.conflict_detail is not None:
                raise ValueError("a dispatch decision cannot carry replay or conflict payload")
        elif self.decision in resumable:
            if self.reservation is None:
                raise ValueError("a resume decision requires a reservation")
            if self.reservation_handle is not None:
                raise ValueError("a resume decision never reissues a handle")
            if self.replay_outcome is not None or self.conflict_detail is not None:
                raise ValueError("a resume decision cannot carry replay or conflict payload")
        elif self.decision is ReservationDecision.EXACT_REPLAY:
            if self.replay_outcome is None:
                raise ValueError("EXACT_REPLAY requires replay_outcome")
            if self.reservation is not None or self.reservation_handle is not None:
                raise ValueError("EXACT_REPLAY never dispatches a child")
            if self.conflict_detail is not None:
                raise ValueError("EXACT_REPLAY cannot carry conflict_detail")
        else:
            if not self.conflict_detail:
                raise ValueError("CONFLICT requires conflict_detail")
            if (
                self.reservation is not None
                or self.reservation_handle is not None
                or self.replay_outcome is not None
            ):
                raise ValueError("CONFLICT cannot carry reservation, handle, or replay payload")


@dataclass(frozen=True, slots=True)
class AuditPrepareRequest:
    """Record semantic acceptance/rejection and, when accepted, prepared effects."""

    attempt_id: AuditAttemptId
    installation_version: InstallationVersion
    semantic_digest: str
    accepted: bool
    effects: tuple[AuditPreparedEffect, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, AuditAttemptId):
            raise ValueError("AuditPrepareRequest.attempt_id has the wrong type")
        if not isinstance(self.installation_version, InstallationVersion):
            raise ValueError("AuditPrepareRequest.installation_version has the wrong type")
        _require_digest("AuditPrepareRequest.semantic_digest", self.semantic_digest)
        effects = tuple(self.effects)
        if any(not isinstance(effect, AuditPreparedEffect) for effect in effects):
            raise ValueError("AuditPrepareRequest.effects must contain AuditPreparedEffect")
        object.__setattr__(self, "effects", effects)
        if not self.accepted and effects:
            raise ValueError("a rejected attempt cannot carry prepared effects")


@dataclass(frozen=True, slots=True)
class AuditPrepareOutcome:
    accepted: bool
    attempt_id: AuditAttemptId
    conflict_detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, AuditAttemptId):
            raise ValueError("AuditPrepareOutcome.attempt_id has the wrong type")
        if self.accepted and self.conflict_detail is not None:
            raise ValueError("an accepted prepare outcome cannot carry conflict_detail")


@dataclass(frozen=True, slots=True)
class AuditFinalCommitRequest:
    """Final CAS: installation version, attempt state, and expected head."""

    attempt_id: AuditAttemptId
    installation_version: InstallationVersion
    expected_head_digest: str | None
    new_head: AuditCycleHead
    preflight_step_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, AuditAttemptId):
            raise ValueError("AuditFinalCommitRequest.attempt_id has the wrong type")
        if not isinstance(self.installation_version, InstallationVersion):
            raise ValueError("AuditFinalCommitRequest.installation_version has the wrong type")
        _require_optional_digest(
            "AuditFinalCommitRequest.expected_head_digest",
            self.expected_head_digest,
        )
        if not isinstance(self.new_head, AuditCycleHead):
            raise ValueError("AuditFinalCommitRequest.new_head has the wrong type")
        names = tuple(self.preflight_step_names)
        if not names or any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("AuditFinalCommitRequest.preflight_step_names must be non-empty")
        object.__setattr__(self, "preflight_step_names", names)


@dataclass(frozen=True, slots=True)
class AuditFinalCommitOutcome:
    committed: bool
    attempt_id: AuditAttemptId
    conflict_detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, AuditAttemptId):
            raise ValueError("AuditFinalCommitOutcome.attempt_id has the wrong type")
        if self.committed and self.conflict_detail is not None:
            raise ValueError("a committed final outcome cannot carry conflict_detail")
        if not self.committed and not self.conflict_detail:
            raise ValueError("a rejected final outcome requires conflict_detail")


@dataclass(frozen=True, slots=True)
class AuditPreflightProjection:
    plan_set_id: str
    scope_id: str
    part_id: str

    def __post_init__(self) -> None:
        for name in ("plan_set_id", "scope_id", "part_id"):
            _require_nonempty(f"AuditPreflightProjection.{name}", getattr(self, name))


@dataclass(frozen=True, slots=True)
class AuditDispositionCommitRequest:
    recipe_execution_id: RecipeExecutionId
    installation_version: InstallationVersion
    cycle_id: str
    scope_id: str
    part_id: str
    authority_digest: str
    plan_digest: str
    report_digest: str
    report_path: Path
    association_digest: str
    association_path: Path
    generated_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.recipe_execution_id, RecipeExecutionId):
            raise ValueError(
                "AuditDispositionCommitRequest.recipe_execution_id has the wrong type"
            )
        if not isinstance(self.installation_version, InstallationVersion):
            raise ValueError(
                "AuditDispositionCommitRequest.installation_version has the wrong type"
            )
        for name in ("cycle_id", "scope_id", "part_id"):
            _require_nonempty(f"AuditDispositionCommitRequest.{name}", getattr(self, name))
        for name in ("authority_digest", "plan_digest", "report_digest", "association_digest"):
            _require_digest(f"AuditDispositionCommitRequest.{name}", getattr(self, name))
        _require_absolute_path(
            "AuditDispositionCommitRequest.report_path",
            self.report_path,
        )
        _require_absolute_path(
            "AuditDispositionCommitRequest.association_path",
            self.association_path,
        )
        _require_nonempty(
            "AuditDispositionCommitRequest.generated_at",
            self.generated_at,
        )


@dataclass(frozen=True, slots=True)
class AuditDispositionCommitOutcome:
    committed: bool
    generated_at: str
    conflict_detail: str | None = None

    def __post_init__(self) -> None:
        if self.committed and self.conflict_detail is not None:
            raise ValueError("a committed disposition outcome cannot carry conflict_detail")
        if not self.committed and not self.conflict_detail:
            raise ValueError("a rejected disposition outcome requires conflict_detail")
        _require_nonempty("AuditDispositionCommitOutcome.generated_at", self.generated_at)


@runtime_checkable
class AuditAdmissionLedger(Protocol):
    """Durable, single-mutable-authority store for audit admission state.

    The v1 indefinite policy retains installation occurrences, slots, attempts,
    finalization acknowledgements, and committed replay projections without a
    lossy compaction transition.
    """

    retention_policy_id: ClassVar[Literal["audit-admission-retention:indefinite:v1"]]

    def store_health(self) -> AuditAdmissionStoreHealth: ...

    def recover_all(self) -> AuditAdmissionRecoveryResult: ...

    def create_or_get_installation(
        self,
        *,
        recipe_execution_id: RecipeExecutionId,
        snapshot_digest: str,
    ) -> InstallationVersion: ...

    def retire_installation(
        self,
        *,
        recipe_execution_id: RecipeExecutionId,
        installation_version: InstallationVersion,
    ) -> None: ...

    def reserve(self, request: AuditReservationRequest) -> AuditReservationOutcome: ...

    def resolve_reservation_handle(
        self,
        reservation_handle: str,
    ) -> AuditIdentityReservation | None: ...

    def prepare(self, request: AuditPrepareRequest) -> AuditPrepareOutcome: ...

    def commit_authority(self, request: AuditFinalCommitRequest) -> AuditFinalCommitOutcome: ...

    def finalize_response(
        self,
        attempt_id: AuditAttemptId,
        outcome: AuditOutcome,
        *,
        required_effect_names: tuple[str, ...],
    ) -> None: ...

    def finalization_effect_result(
        self,
        attempt_id: AuditAttemptId,
        effect_name: str,
    ) -> dict[str, Any] | None: ...

    def acknowledge_finalization_effect(
        self,
        attempt_id: AuditAttemptId,
        effect_name: str,
        result: dict[str, Any],
    ) -> None: ...

    def current_head(
        self,
        *,
        recipe_execution_id: RecipeExecutionId,
        cycle_id: str,
        scope_id: str,
        part_id: str,
    ) -> AuditCycleHead | None: ...

    def preflight_projection(
        self,
        *,
        recipe_execution_id: RecipeExecutionId,
        installation_version: InstallationVersion,
        step_name: str,
    ) -> AuditPreflightProjection | None: ...

    def commit_disposition(
        self,
        request: AuditDispositionCommitRequest,
    ) -> AuditDispositionCommitOutcome: ...

    def resolve_disposition(
        self,
        *,
        authority_digest: str,
        plan_digest: str,
    ) -> Path | None: ...
