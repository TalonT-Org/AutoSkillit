"""Unified capture admission, evidence, and compacted-byte capacity policy."""

from __future__ import annotations

from collections.abc import Mapping

from . import _ledger
from ._failure_policy import CaptureFailureReason
from ._module_identity import register_module_aliases
from ._types import CaptureCapacityReason, CaptureCapacitySpec

register_module_aliases(__name__)

Record = _ledger.CaptureLifecycleRecord

_REASON_DETAILS = {
    CaptureCapacityReason.ACTIVE_CAPACITY: "active lifecycle record bound reached",
    CaptureCapacityReason.RETENTION_CAPACITY: "retention capacity reached",
    CaptureCapacityReason.EVIDENCE_CAPACITY: "evidence record capacity reached",
    CaptureCapacityReason.PROJECTED_COMPACTED_BYTES: "compacted lifecycle capacity reached",
    CaptureCapacityReason.HARD_LEDGER_CAPACITY: "hard lifecycle ledger capacity reached",
}

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


def compacted_bytes(
    records: Mapping[str, Record],
    compaction_epoch: int,
    spec: CaptureCapacitySpec,
) -> int:
    return sum(
        len(
            _ledger.encode_frame(
                _ledger.record_to_dict(record),
                compaction_epoch=compaction_epoch,
            )
        )
        for record in compacted_records(records, spec)
    )


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
    encoded = compacted_bytes(projected, compaction_epoch, spec)
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
) -> CaptureCapacityReason | None:
    projected = _projected(records, candidate)
    encoded = compacted_bytes(projected, compaction_epoch, spec)
    recovery_state = transition_compaction_bound(candidate, spec) == spec.hard_ledger_bytes
    if encoded > transition_compaction_bound(candidate, spec):
        return CaptureCapacityReason.HARD_LEDGER_CAPACITY
    if not recovery_state and encoded > spec.compaction_low_bytes:
        return CaptureCapacityReason.PROJECTED_COMPACTED_BYTES
    return None
