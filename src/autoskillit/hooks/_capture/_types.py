"""Shell-capture lifecycle result and internal signal types."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from . import _descriptor, _failure_policy
from ._module_identity import register_module_aliases

register_module_aliases(__name__)

__all__ = [
    "CaptureCapacitySpec",
    "CaptureCapacityReason",
    "CaptureCleanupOutcome",
    "CaptureFailureEvidence",
    "CleanupBlocker",
    "CleanupProgress",
    "DueKey",
    "LedgerIncarnation",
    "LedgerSnapshot",
    "LegacyCleanupOnly",
    "SweepAttempt",
    "SweepBudgetSpec",
]


@dataclass(frozen=True, slots=True, order=True)
class DueKey:
    next_attempt_at: float
    capture_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.next_attempt_at, (int, float))
            or isinstance(self.next_attempt_at, bool)
            or not math.isfinite(self.next_attempt_at)
            or not isinstance(self.capture_id, str)
            or not self.capture_id
        ):
            raise ValueError("invalid lifecycle due key")


@dataclass(frozen=True, slots=True)
class LedgerIncarnation:
    device: int
    inode: int
    compaction_epoch: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < minimum
            for value, minimum in (
                (self.device, 0),
                (self.inode, 0),
                (self.compaction_epoch, 1),
            )
        ):
            raise ValueError("invalid lifecycle ledger incarnation")


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    size: int
    ctime_ns: int
    decoded_offset: int

    def __post_init__(self) -> None:
        if (
            any(type(value) is not int or value < 0 for value in (self.size, self.ctime_ns))
            or type(self.decoded_offset) is not int
            or self.decoded_offset < 0
            or self.decoded_offset > self.size
        ):
            raise ValueError("invalid lifecycle ledger snapshot")


@dataclass(frozen=True, slots=True)
class SweepBudgetSpec:
    max_records_inspected: int = 4096
    max_replay_bytes: int = 4 * 1024 * 1024
    max_attempts: int = 32
    max_transitions: int = 128
    max_cursor_writes: int = 32
    max_duration_seconds: float = 0.05

    def __post_init__(self) -> None:
        integer_values = (
            self.max_records_inspected,
            self.max_replay_bytes,
            self.max_attempts,
            self.max_transitions,
            self.max_cursor_writes,
        )
        if (
            any(type(value) is not int or value <= 0 for value in integer_values)
            or not isinstance(self.max_duration_seconds, (int, float))
            or isinstance(self.max_duration_seconds, bool)
            or not math.isfinite(self.max_duration_seconds)
            or self.max_duration_seconds <= 0
        ):
            raise ValueError("cleanup bounds must be positive and finite")


@dataclass(frozen=True, slots=True)
class CaptureCapacitySpec:
    max_operational_records: int = 4096
    max_retained_records: int = 4096
    max_forensic_records: int = 4096
    max_tombstones: int = 256
    compaction_low_bytes: int = 15 * 1024 * 1024 // 4
    compaction_high_bytes: int = 31 * 1024 * 1024 // 8
    hard_ledger_bytes: int = 4 * 1024 * 1024
    cursor_headroom_bytes: int = 4 * 1024
    tamper_headroom_bytes: int = 32 * 1024
    reclamation_headroom_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        integer_values = tuple(getattr(self, field) for field in self.__dataclass_fields__)
        recovery_headroom = (
            self.cursor_headroom_bytes
            + self.tamper_headroom_bytes
            + self.reclamation_headroom_bytes
        )
        if (
            any(type(value) is not int or value <= 0 for value in integer_values)
            or self.max_retained_records > self.max_operational_records
            or not self.compaction_low_bytes < self.compaction_high_bytes
            or self.compaction_high_bytes + recovery_headroom > self.hard_ledger_bytes
        ):
            raise ValueError("invalid capture capacity specification")


class CaptureCapacityReason(StrEnum):
    ACTIVE_CAPACITY = "active_capacity_exhausted"
    RETENTION_CAPACITY = "retention_capacity_exhausted"
    FORENSIC_EVIDENCE = "forensic_evidence_exhausted"
    PROJECTED_COMPACTED_BYTES = "projected_compacted_bytes_exhausted"
    HARD_LEDGER_CAPACITY = "hard_ledger_capacity_exhausted"


class SweepAttempt(StrEnum):
    DELETED = "deleted"
    CARRIER_LEASE_LIVE = "carrier_lease_live"
    NOT_DUE = "not_due"
    TAMPERED = "tampered"
    ERROR = "error"


class CleanupProgress(StrEnum):
    NONE = "none"
    CURSOR_REPAIRED = "cursor_repaired"
    CURSOR_ADVANCED = "cursor_advanced"
    TRANSITIONED = "transitioned"
    RETIRED = "retired"


class CleanupBlocker(StrEnum):
    NONE = "none"
    STORE_ABSENT = "store_absent"
    LOCK_CONTENDED = "lock_contended"
    FILESYSTEM_AUTHORITY = "filesystem_authority"
    PERMISSION_DENIED = "permission_denied"
    FILESYSTEM_IO = "filesystem_io"
    LEDGER_INTEGRITY = "ledger_integrity"
    MIGRATION_BLOCKED = "migration_blocked"
    RECORD_BUDGET = "record_budget"
    REPLAY_BYTE_BUDGET = "replay_byte_budget"
    ATTEMPT_BUDGET = "attempt_budget"
    TRANSITION_BUDGET = "transition_budget"
    CURSOR_WRITE_BUDGET = "cursor_write_budget"
    ELAPSED_DEADLINE = "elapsed_deadline"


class SweepBudgetExceeded(Exception):
    def __init__(self, blocker: CleanupBlocker) -> None:
        super().__init__(blocker.value)
        self.blocker = blocker


@dataclass(frozen=True, slots=True)
class CaptureCleanupOutcome:
    examined: int = 0
    deleted: int = 0
    deleted_bytes: int = 0
    carrier_lease_live: int = 0
    not_due: int = 0
    tampered: int = 0
    errors: int = 0
    retry_count: int = 0
    remaining_due: int = 0
    records_inspected: int = 0
    replay_bytes: int = 0
    transitions: int = 0
    cursor_writes: int = 0
    progress: CleanupProgress = CleanupProgress.NONE
    blocker: CleanupBlocker = CleanupBlocker.NONE
    duration: float = 0.0


@dataclass(frozen=True, slots=True)
class CaptureFailureEvidence:
    stage: str
    detail: str
    settlement_returncode: int | None = None

    def __post_init__(self) -> None:
        if (
            not _failure_policy.valid_failure_stage(self.stage)
            or not _failure_policy.valid_failure_detail(self.detail)
            or (
                self.settlement_returncode is not None
                and (type(self.settlement_returncode) is not int)
            )
        ):
            raise _descriptor.CaptureAuthorityError("invalid capture failure evidence")


@dataclass(frozen=True, slots=True)
class LegacyCleanupOnly:
    """Bounded legacy deletion evidence that carries no snapshot authority."""

    observed_size: int

    def __post_init__(self) -> None:
        if type(self.observed_size) is not int or self.observed_size < 0:
            raise _descriptor.CaptureAuthorityError("invalid legacy cleanup observation")


@dataclass(frozen=True, slots=True)
class ObservedArtifact:
    fd: int
    identity: tuple[int, int]
    nlink: int
    size: int


class CarrierLeaseLive(Exception):
    pass


class LockContended(RuntimeError):
    def __init__(self) -> None:
        super().__init__("capture recovery lock is contended")
        self.reason = _failure_policy.CaptureFailureReason.RECOVERY_CONTENDED


class Tampered(Exception):
    pass
