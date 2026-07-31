"""Typed internal ports for shell-capture lifecycle shards."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol, TypeAlias

from . import _ledger, _snapshot
from ._module_identity import register_module_aliases
from ._types import CaptureCapacitySpec, ObservedArtifact, SweepBudgetSpec

register_module_aliases(__name__)

Record: TypeAlias = _ledger.CaptureLifecycleRecord
Records: TypeAlias = dict[str, Record]
LoadedLedger: TypeAlias = tuple[Records, int, int]
Transition: TypeAlias = Callable[[Record], Record]
DeliveryValue: TypeAlias = (
    _snapshot.FinalizedCapture
    | _snapshot.PublishedCaptureReference
    | _snapshot.UnavailableCaptureReference
)


class LedgerReadStorePort(Protocol):
    def _locked(
        self,
        *,
        blocking: bool = True,
    ) -> AbstractContextManager[None]: ...

    def _load_locked(self) -> LoadedLedger: ...


class TransitionStorePort(LedgerReadStorePort, Protocol):
    @staticmethod
    def _authority_for(record: Record) -> _snapshot.CaptureWriteAuthority: ...

    def _transition_locked(
        self,
        *,
        records: Records,
        compaction_epoch: int,
        ledger_size: int,
        authority: _snapshot.CaptureWriteAuthority,
        allowed_states: set[_ledger.CaptureState],
        transform: Transition,
    ) -> Record: ...


class DeliveryStorePort(Protocol):
    def get_record(self, capture_id: str) -> Record | None: ...

    def mark_reference_unavailable(
        self,
        finalized: _snapshot.FinalizedCapture,
        *,
        reason_code: str,
    ) -> _snapshot.UnavailableCaptureReference: ...

    def transition_delivery(
        self,
        value: DeliveryValue,
        *,
        expected: _ledger.CaptureDeliveryStatus,
        target: _ledger.CaptureDeliveryStatus,
    ) -> Record: ...

    def mark_delivery_unknown(self, value: DeliveryValue) -> Record: ...

    def _transition_current(
        self,
        capture_id: str,
        incarnation: str,
        *,
        allowed_states: set[_ledger.CaptureState],
        transform: Transition,
    ) -> Record: ...


class DeliveryNormalizationStorePort(TransitionStorePort, Protocol):
    def _acquire_cleanup_lease(
        self,
        record: Record,
    ) -> ObservedArtifact | None: ...


class ResolverStorePort(LedgerReadStorePort, Protocol):
    _root_fd: int
    _project_identity: tuple[int, int]
    _root_identity: tuple[int, int]
    _wall_clock: Callable[[], float]
    _sweep_budget: SweepBudgetSpec | None


class SweepStorePort(TransitionStorePort, Protocol):
    _root_fd: int
    _project_identity: tuple[int, int]
    _root_identity: tuple[int, int]
    _wall_clock: Callable[[], float]
    _sweep_budget: SweepBudgetSpec | None
    _sweep_records_inspected: int
    _sweep_replay_bytes: int
    _sweep_transitions: int
    _sweep_cursor_writes: int

    def _acquire_cleanup_lease(
        self,
        record: Record,
    ) -> ObservedArtifact | None: ...

    def _normalize_abandoned(
        self,
        record: Record,
        *,
        preleased: ObservedArtifact | None = None,
        lease_checked: bool = False,
    ) -> tuple[Record, ObservedArtifact | None]: ...

    def _deleting_record(self, record: Record) -> Record: ...

    def _quarantine_delete(
        self,
        record: Record,
        authorize_delete: Callable[[], None] | None = None,
        *,
        preleased: ObservedArtifact | None = None,
        lease_checked: bool = False,
    ) -> int: ...


class MigrationStorePort(SweepStorePort, Protocol):
    _capacity: CaptureCapacitySpec

    def _compact_locked(
        self,
        records: Records,
        compaction_epoch: int,
    ) -> None: ...
