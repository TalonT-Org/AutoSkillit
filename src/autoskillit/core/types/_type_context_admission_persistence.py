"""Process-local control surface for context-admission persistence.

Defines the store authority, health projections, operation-result
dataclasses, and the ``ContextAdmissionLedger`` Protocol.  The durable
envelope boundary types live in
``_type_context_admission_persistence_envelope``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ._type_context_admission import (
    AcceptInputEvent,
    AdmissionDecision,
    AdmissionEffect,
    AdmissionTransition,
    ContextAdmissionEvent,
    ContextAdmissionState,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
)
from ._type_context_admission_persistence_envelope import (
    ContextAdmissionStreamKey,
    ShadowContextAdmissionRecord,
)
from ._type_enums import (
    ContextAdmissionAccountingStatus,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
)
from ._type_helpers import (
    _validate_non_negative,
    _validate_reason_code,
)


@dataclass(frozen=True, slots=True)
class ContextAdmissionStoreAuthority:
    """Process-local authority for opening one context-admission store."""

    database_path: Path
    expected_owner_id: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.database_path, Path)
            or not self.database_path.is_absolute()
            or not self.database_path.name
        ):
            raise ValueError("invalid_context_admission_store_path")
        if (
            not isinstance(self.expected_owner_id, int)
            or isinstance(self.expected_owner_id, bool)
            or self.expected_owner_id < 0
        ):
            raise ValueError("invalid_context_admission_store_owner")


@dataclass(frozen=True, slots=True)
class ContextAdmissionStoreHealth:
    status: ContextAdmissionStorageHealthStatus
    failure_reason: ContextAdmissionStorageFailureReason | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _validate_health(self.status, self.failure_reason, self.reason_code)


@dataclass(frozen=True, slots=True)
class ContextAdmissionStreamHealth:
    stream_key: ContextAdmissionStreamKey
    status: ContextAdmissionStorageHealthStatus
    failure_reason: ContextAdmissionStorageFailureReason | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stream_key, ContextAdmissionStreamKey):
            raise ValueError("invalid_context_admission_stream_key")
        _validate_health(self.status, self.failure_reason, self.reason_code)


def _validate_health(
    status: ContextAdmissionStorageHealthStatus,
    failure_reason: ContextAdmissionStorageFailureReason | None,
    reason_code: str | None,
) -> None:
    if not isinstance(status, ContextAdmissionStorageHealthStatus):
        raise ValueError("invalid_context_admission_storage_health")
    if failure_reason is not None and not isinstance(
        failure_reason,
        ContextAdmissionStorageFailureReason,
    ):
        raise ValueError("invalid_context_admission_storage_health")
    failed = status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED
    if failed != (failure_reason is not None):
        raise ValueError("invalid_context_admission_storage_health")
    if failed != (reason_code is not None):
        raise ValueError("invalid_context_admission_storage_health")
    if reason_code is not None:
        _validate_reason_code(reason_code)


@dataclass(frozen=True, slots=True)
class ContextAdmissionAccountingResult:
    status: ContextAdmissionAccountingStatus
    stream_key: ContextAdmissionStreamKey
    transition: AdmissionTransition | None = None
    journal_sequence: int | None = None
    failure_reason: ContextAdmissionStorageFailureReason | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ContextAdmissionAccountingStatus):
            raise ValueError("invalid_context_admission_accounting_status")
        if not isinstance(self.stream_key, ContextAdmissionStreamKey):
            raise ValueError("invalid_context_admission_stream_key")
        if self.failure_reason is not None and not isinstance(
            self.failure_reason,
            ContextAdmissionStorageFailureReason,
        ):
            raise ValueError("invalid_context_admission_storage_failure_reason")
        if self.journal_sequence is not None:
            _validate_non_negative(self.journal_sequence, "invalid_journal_sequence")
            if self.journal_sequence == 0:
                raise ValueError("invalid_journal_sequence")
        if self.reason_code is not None:
            _validate_reason_code(self.reason_code)
        if self.status is ContextAdmissionAccountingStatus.RECORDED:
            if self.transition is None or self.journal_sequence is None:
                raise ValueError("recorded_result_requires_publication")
        elif self.status is ContextAdmissionAccountingStatus.EXACT_REPLAY:
            if self.transition is None or self.transition.effects or self.journal_sequence is None:
                raise ValueError("exact_replay_result_is_not_idempotent")
        elif self.status is ContextAdmissionAccountingStatus.SEMANTIC_REJECTION:
            if self.transition is None:
                raise ValueError("semantic_rejection_requires_transition")
        elif self.status in {
            ContextAdmissionAccountingStatus.RECONCILIATION_REQUIRED,
            ContextAdmissionAccountingStatus.PROTOCOL_QUARANTINED,
        }:
            if self.transition is None or self.journal_sequence is None:
                raise ValueError("published_result_requires_transition")
        elif self.status in {
            ContextAdmissionAccountingStatus.CONTENDED,
            ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
        }:
            if self.transition is not None or self.journal_sequence is not None:
                raise ValueError("nonadmitting_storage_result_has_transition")
        if self.status is ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED:
            if self.failure_reason is None or self.reason_code is None:
                raise ValueError("storage_failure_requires_reason")
        elif self.failure_reason is not None:
            raise ValueError("nonstorage_result_has_storage_reason")
        if (
            self.transition is not None
            and self.reason_code != self.transition.decision.reason_code
        ):
            raise ValueError("accounting_reason_code_mismatch")


@dataclass(frozen=True, slots=True)
class ContextAdmissionRecoveryResult:
    status: ContextAdmissionStorageHealthStatus
    store_health: ContextAdmissionStoreHealth
    stream_healths: tuple[ContextAdmissionStreamHealth, ...]
    recovered_streams: tuple[ContextAdmissionStreamKey, ...]
    unresolved_streams: tuple[ContextAdmissionStreamKey, ...]

    def __post_init__(self) -> None:
        if self.status is not self.store_health.status:
            raise ValueError("recovery_status_mismatch")
        health_keys = tuple(health.stream_key for health in self.stream_healths)
        recovered_keys = set(self.recovered_streams)
        unresolved_keys = set(self.unresolved_streams)
        if (
            len(set(health_keys)) != len(health_keys)
            or len(recovered_keys) != len(self.recovered_streams)
            or len(unresolved_keys) != len(self.unresolved_streams)
        ):
            raise ValueError("duplicate_recovery_stream_projection")
        healthy_keys = {
            health.stream_key
            for health in self.stream_healths
            if health.status is ContextAdmissionStorageHealthStatus.HEALTHY
        }
        if recovered_keys != healthy_keys:
            raise ValueError("recovered_stream_projection_mismatch")
        if not unresolved_keys <= recovered_keys:
            raise ValueError("unresolved_stream_projection_mismatch")


@dataclass(frozen=True, slots=True)
class ContextAdmissionInspectionResult:
    stream_key: ContextAdmissionStreamKey
    health: ContextAdmissionStreamHealth
    state: ContextAdmissionState | None
    events: tuple[ContextAdmissionEvent, ...]
    decisions: tuple[AdmissionDecision, ...]
    effects: tuple[tuple[AdmissionEffect, ...], ...]
    shadows: tuple[ShadowContextAdmissionRecord, ...]
    latest_journal_sequence: int

    def __post_init__(self) -> None:
        if self.stream_key != self.health.stream_key:
            raise ValueError("inspection_stream_health_identity_mismatch")
        if (self.health.status is ContextAdmissionStorageHealthStatus.HEALTHY) != (
            self.state is not None
        ):
            raise ValueError("inspection_health_state_mismatch")
        _validate_non_negative(
            self.latest_journal_sequence,
            "invalid_latest_journal_sequence",
        )
        lengths = {
            len(self.events),
            len(self.decisions),
            len(self.effects),
            len(self.shadows),
        }
        if len(lengths) != 1 or lengths != {self.latest_journal_sequence}:
            raise ValueError("inspection_publication_length_mismatch")


@runtime_checkable
class ContextAdmissionLedger(Protocol):
    """Durable reducer publication and recovery service."""

    def store_health(self) -> ContextAdmissionStoreHealth: ...

    def stream_health(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionStreamHealth: ...

    def apply(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ContextAdmissionEvent,
    ) -> ContextAdmissionAccountingResult: ...

    def reserve(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ReserveRequestEvent,
    ) -> ContextAdmissionAccountingResult: ...

    def commit(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: (AcceptInputEvent | ResolveIndeterminateAcceptedEvent | ReconcileGenerationEvent),
    ) -> ContextAdmissionAccountingResult: ...

    def release(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: (
            ReleaseNonAdmissionEvent
            | RollbackAdmissionEvent
            | ResolveIndeterminateNonAdmissionEvent
            | ResolveIndeterminateRollbackEvent
        ),
    ) -> ContextAdmissionAccountingResult: ...

    def recover(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionRecoveryResult: ...

    def recover_all(self) -> ContextAdmissionRecoveryResult: ...

    def replay(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult: ...

    def inspect_stream(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult: ...


__all__ = [
    "ContextAdmissionAccountingResult",
    "ContextAdmissionInspectionResult",
    "ContextAdmissionLedger",
    "ContextAdmissionRecoveryResult",
    "ContextAdmissionStoreAuthority",
    "ContextAdmissionStoreHealth",
    "ContextAdmissionStreamHealth",
]
