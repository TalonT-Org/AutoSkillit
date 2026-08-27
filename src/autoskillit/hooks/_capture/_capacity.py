"""Unified capture admission, evidence, and compacted-byte capacity policy."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from . import _ledger
from ._failure_policy import CAPACITY_FAILURE_REASONS, CaptureFailureReason
from ._module_identity import register_module_aliases
from ._types import CaptureCapacityReason, CaptureCapacitySpec

register_module_aliases(__name__)


# ---------------------------------------------------------------------------
# Gate declaration
# ---------------------------------------------------------------------------


class CapacityGate(StrEnum):
    """Which capacity gate a reason can fire from."""

    ADMISSION = "ADMISSION"
    TRANSITION = "TRANSITION"


CAPACITY_REASON_GATES: dict[CaptureCapacityReason, frozenset[CapacityGate]] = {
    CaptureCapacityReason.ACTIVE_CAPACITY: frozenset({CapacityGate.ADMISSION}),
    CaptureCapacityReason.RETENTION_CAPACITY: frozenset({CapacityGate.ADMISSION}),
    CaptureCapacityReason.EVIDENCE_CAPACITY: frozenset({CapacityGate.ADMISSION}),
    CaptureCapacityReason.PROJECTED_COMPACTED_BYTES: frozenset(
        {CapacityGate.ADMISSION, CapacityGate.TRANSITION}
    ),
    CaptureCapacityReason.HARD_LEDGER_CAPACITY: frozenset(
        {CapacityGate.ADMISSION, CapacityGate.TRANSITION}
    ),
}

# Import-time totality assertion: a new CaptureCapacityReason member
# without a gate declaration prevents this module from loading.
if set(CAPACITY_REASON_GATES) != set(CaptureCapacityReason):
    raise AssertionError(
        "CAPACITY_REASON_GATES must cover exactly the CaptureCapacityReason members: "
        f"missing={set(CaptureCapacityReason) - set(CAPACITY_REASON_GATES)}, "
        f"extra={set(CAPACITY_REASON_GATES) - set(CaptureCapacityReason)}"
    )
for _key, _gates in CAPACITY_REASON_GATES.items():
    if not _gates:
        raise AssertionError(f"CAPACITY_REASON_GATES[{_key!r}] has empty gate set")

Record = _ledger.CaptureLifecycleRecord


class CompactedFrameSizer:
    """Memoize compacted ledger frame sizes for one materialized ledger view."""

    def __init__(self) -> None:
        self._frame_sizes: dict[str, tuple[Record, int, int]] = {}

    def reset(self) -> None:
        self._frame_sizes.clear()

    def retain_records(self, records: Mapping[str, Record]) -> None:
        for capture_id in tuple(self._frame_sizes):
            if capture_id not in records:
                del self._frame_sizes[capture_id]

    def hydrate(
        self,
        records: Mapping[str, Record],
        compaction_epoch: int,
        frame_sizes: Mapping[str, int],
    ) -> None:
        for capture_id, size in frame_sizes.items():
            record = records.get(capture_id)
            if record is not None:
                self._frame_sizes[capture_id] = (record, compaction_epoch, size)

    def total_bytes(
        self,
        records: Mapping[str, Record],
        compaction_epoch: int,
        spec: CaptureCapacitySpec,
    ) -> int:
        compacted = compacted_records(records, spec)
        retained_ids = {record.capture_id for record in compacted}
        for capture_id in tuple(self._frame_sizes):
            if capture_id not in retained_ids:
                del self._frame_sizes[capture_id]

        total = 0
        for record in compacted:
            cached = self._frame_sizes.get(record.capture_id)
            if cached is not None and cached[:2] == (record, compaction_epoch):
                total += cached[2]
                continue
            size = len(
                _ledger.encode_frame(
                    _ledger.record_to_dict(record),
                    compaction_epoch=compaction_epoch,
                )
            )
            self._frame_sizes[record.capture_id] = (record, compaction_epoch, size)
            total += size
        return total


_REASON_DETAILS = {
    CaptureCapacityReason.ACTIVE_CAPACITY: "active lifecycle record bound reached",
    CaptureCapacityReason.RETENTION_CAPACITY: "retention capacity reached",
    CaptureCapacityReason.EVIDENCE_CAPACITY: "evidence record capacity reached",
    CaptureCapacityReason.PROJECTED_COMPACTED_BYTES: "compacted lifecycle capacity reached",
    CaptureCapacityReason.HARD_LEDGER_CAPACITY: "hard lifecycle ledger capacity reached",
}
if set(_REASON_DETAILS) != set(CaptureCapacityReason):
    raise AssertionError("_REASON_DETAILS must cover exactly the CaptureCapacityReason members")

_FAILURE_REASONS = {
    CaptureCapacityReason.ACTIVE_CAPACITY: CaptureFailureReason.ACTIVE_CAPACITY_EXHAUSTED,
    CaptureCapacityReason.RETENTION_CAPACITY: CaptureFailureReason.RETENTION_CAPACITY_EXHAUSTED,
    CaptureCapacityReason.EVIDENCE_CAPACITY: CaptureFailureReason.EVIDENCE_CAPACITY_EXHAUSTED,
    CaptureCapacityReason.PROJECTED_COMPACTED_BYTES: (
        CaptureFailureReason.PROJECTED_COMPACTED_BYTES_EXHAUSTED
    ),
    CaptureCapacityReason.HARD_LEDGER_CAPACITY: (
        CaptureFailureReason.HARD_LEDGER_CAPACITY_EXHAUSTED
    ),
}
if frozenset(_FAILURE_REASONS.values()) != CAPACITY_FAILURE_REASONS:
    raise AssertionError(
        "_FAILURE_REASONS must cover exactly the capacity CaptureFailureReason members"
    )


def reason_detail(reason: CaptureCapacityReason) -> str:
    return _REASON_DETAILS[reason]


def failure_reason(reason: CaptureCapacityReason) -> CaptureFailureReason:
    return _FAILURE_REASONS[reason]


def transition_compaction_bound(
    candidate: Record | None,
    spec: CaptureCapacitySpec,
) -> int:
    if candidate is not None and candidate.state in {
        _ledger.CaptureState.DELETING,
        _ledger.CaptureState.DELETED,
        _ledger.CaptureState.TAMPERED,
    }:
        return spec.hard_ledger_bytes
    return spec.hard_ledger_bytes - spec.recovery_headroom_bytes


def compacted_records(
    records: Mapping[str, Record],
    spec: CaptureCapacitySpec,
) -> list[Record]:
    live = [
        record for record in records.values() if record.state is not _ledger.CaptureState.DELETED
    ]
    tombstones = sorted(
        (record for record in records.values() if record.state is _ledger.CaptureState.DELETED),
        key=lambda record: (record.next_attempt_at, record.capture_id),
        reverse=True,
    )[: spec.max_tombstones]
    return sorted(live + tombstones, key=lambda record: record.capture_id)


def _projected(
    records: Mapping[str, Record],
    candidate: Record,
) -> dict[str, Record]:
    projected = dict(records)
    projected[candidate.capture_id] = candidate
    return projected


def admission_reason(
    records: Mapping[str, Record],
    candidate: Record,
    *,
    compaction_epoch: int,
    spec: CaptureCapacitySpec,
    active_limit: int,
    sizer: CompactedFrameSizer,
) -> CaptureCapacityReason | None:
    projected = _projected(records, candidate)
    operational = sum(
        record.state not in {_ledger.CaptureState.DELETED, _ledger.CaptureState.TAMPERED}
        for record in projected.values()
    )
    retained = sum(
        record.state not in {_ledger.CaptureState.DELETED, _ledger.CaptureState.TAMPERED}
        and record.retention_phase is _ledger.CaptureRetentionPhase.ACTIVE
        for record in projected.values()
    )
    forensic = sum(record.state is _ledger.CaptureState.TAMPERED for record in projected.values())
    if operational > active_limit:
        return CaptureCapacityReason.ACTIVE_CAPACITY
    if retained > spec.max_retained_records:
        return CaptureCapacityReason.RETENTION_CAPACITY
    if operational + forensic > spec.max_evidence_records:
        return CaptureCapacityReason.EVIDENCE_CAPACITY
    encoded = sizer.total_bytes(projected, compaction_epoch, spec)
    if encoded + spec.recovery_headroom_bytes > spec.hard_ledger_bytes:
        return CaptureCapacityReason.HARD_LEDGER_CAPACITY
    if encoded > spec.compaction_low_bytes:
        return CaptureCapacityReason.PROJECTED_COMPACTED_BYTES
    return None


def transition_reason(
    records: Mapping[str, Record],
    candidate: Record,
    *,
    compaction_epoch: int,
    spec: CaptureCapacitySpec,
    sizer: CompactedFrameSizer,
) -> CaptureCapacityReason | None:
    projected = _projected(records, candidate)
    encoded = sizer.total_bytes(projected, compaction_epoch, spec)
    recovery_state = transition_compaction_bound(candidate, spec) == spec.hard_ledger_bytes
    if encoded > transition_compaction_bound(candidate, spec):
        return CaptureCapacityReason.HARD_LEDGER_CAPACITY
    if not recovery_state and encoded > spec.compaction_low_bytes:
        return CaptureCapacityReason.PROJECTED_COMPACTED_BYTES
    return None
