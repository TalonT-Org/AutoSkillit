"""Canonical lifecycle status enums, successor policy, and reclaimability declarations."""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from ._module_identity import register_module_aliases

register_module_aliases(__name__)

__all__ = [
    "CaptureDeliveryStatus",
    "CaptureReferenceStatus",
    "CaptureRetentionPhase",
    "CaptureSnapshotStatus",
    "CaptureState",
    "CaptureStatus",
    "ReclaimKind",
    "STATE_RECLAIMABILITY",
    "StateReclaimabilityDef",
    "is_delivery_successor",
    "is_reference_successor",
    "is_retention_successor",
    "is_state_successor",
]


class CaptureState(StrEnum):
    RESERVED = "RESERVED"
    STAGED = "STAGED"
    PUBLISHED_WRITING = "PUBLISHED_WRITING"
    FINALIZED = "FINALIZED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"
    DELETING = "DELETING"
    TAMPERED = "TAMPERED"
    DELETED = "DELETED"


class CaptureReferenceStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    ISSUED = "issued"
    PUBLISHED = "published"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    REVOKED = "revoked"


class CaptureDeliveryStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    ATTEMPTING = "attempting"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"


class CaptureRetentionPhase(StrEnum):
    ACTIVE = "active"
    ELIGIBLE = "eligible"
    DELETING = "deleting"
    TAMPERED = "tampered"
    DELETED = "deleted"


class CaptureSnapshotStatus(StrEnum):
    ABSENT = "absent"
    VERIFIED = "verified"


class CaptureStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"
    LEGACY_CLEANUP_ONLY = "legacy_cleanup_only"


_STATE_SUCCESSORS = {
    CaptureState.RESERVED: {
        CaptureState.RESERVED,
        CaptureState.STAGED,
        CaptureState.FAILED,
        CaptureState.ABANDONED,
        CaptureState.DELETED,
        CaptureState.TAMPERED,
    },
    CaptureState.STAGED: {
        CaptureState.STAGED,
        CaptureState.PUBLISHED_WRITING,
        CaptureState.FAILED,
        CaptureState.ABANDONED,
        CaptureState.DELETED,
        CaptureState.TAMPERED,
    },
    CaptureState.PUBLISHED_WRITING: {
        CaptureState.PUBLISHED_WRITING,
        CaptureState.FINALIZED,
        CaptureState.FAILED,
        CaptureState.ABANDONED,
        CaptureState.DELETED,
        CaptureState.TAMPERED,
    },
    CaptureState.FINALIZED: {
        CaptureState.FINALIZED,
        CaptureState.DELETING,
        CaptureState.TAMPERED,
    },
    CaptureState.FAILED: {
        CaptureState.FAILED,
        CaptureState.DELETING,
        CaptureState.TAMPERED,
    },
    CaptureState.ABANDONED: {
        CaptureState.ABANDONED,
        CaptureState.DELETING,
        CaptureState.DELETED,
        CaptureState.TAMPERED,
    },
    CaptureState.DELETING: {
        CaptureState.DELETING,
        CaptureState.DELETED,
        CaptureState.TAMPERED,
    },
    CaptureState.TAMPERED: {CaptureState.TAMPERED, CaptureState.DELETING, CaptureState.DELETED},
    CaptureState.DELETED: {CaptureState.DELETED},
}

_REFERENCE_SUCCESSORS = {
    CaptureReferenceStatus.NOT_REQUESTED: {CaptureReferenceStatus.NOT_REQUESTED},
    CaptureReferenceStatus.ISSUED: {
        CaptureReferenceStatus.ISSUED,
        CaptureReferenceStatus.PUBLISHED,
        CaptureReferenceStatus.UNAVAILABLE,
        CaptureReferenceStatus.UNKNOWN,
        CaptureReferenceStatus.EXPIRED,
        CaptureReferenceStatus.REVOKED,
    },
    CaptureReferenceStatus.PUBLISHED: {
        CaptureReferenceStatus.PUBLISHED,
        CaptureReferenceStatus.UNAVAILABLE,
        CaptureReferenceStatus.UNKNOWN,
        CaptureReferenceStatus.EXPIRED,
        CaptureReferenceStatus.REVOKED,
    },
    CaptureReferenceStatus.UNAVAILABLE: {
        CaptureReferenceStatus.UNAVAILABLE,
        CaptureReferenceStatus.EXPIRED,
        CaptureReferenceStatus.REVOKED,
    },
    CaptureReferenceStatus.UNKNOWN: {
        CaptureReferenceStatus.UNKNOWN,
        CaptureReferenceStatus.EXPIRED,
        CaptureReferenceStatus.REVOKED,
    },
    CaptureReferenceStatus.EXPIRED: {CaptureReferenceStatus.EXPIRED},
    CaptureReferenceStatus.REVOKED: {CaptureReferenceStatus.REVOKED},
}

_DELIVERY_SUCCESSORS = {
    CaptureDeliveryStatus.NOT_ATTEMPTED: {
        CaptureDeliveryStatus.NOT_ATTEMPTED,
        CaptureDeliveryStatus.ATTEMPTING,
        CaptureDeliveryStatus.UNKNOWN,
    },
    CaptureDeliveryStatus.ATTEMPTING: {
        CaptureDeliveryStatus.ATTEMPTING,
        CaptureDeliveryStatus.DELIVERED,
        CaptureDeliveryStatus.FAILED,
        CaptureDeliveryStatus.UNKNOWN,
    },
    CaptureDeliveryStatus.DELIVERED: {CaptureDeliveryStatus.DELIVERED},
    CaptureDeliveryStatus.FAILED: {CaptureDeliveryStatus.FAILED},
    CaptureDeliveryStatus.UNKNOWN: {CaptureDeliveryStatus.UNKNOWN},
}

_RETENTION_SUCCESSORS = {
    CaptureRetentionPhase.ACTIVE: {
        CaptureRetentionPhase.ACTIVE,
        CaptureRetentionPhase.ELIGIBLE,
        CaptureRetentionPhase.DELETING,
        CaptureRetentionPhase.TAMPERED,
        CaptureRetentionPhase.DELETED,
    },
    CaptureRetentionPhase.ELIGIBLE: {
        CaptureRetentionPhase.ELIGIBLE,
        CaptureRetentionPhase.DELETING,
        CaptureRetentionPhase.TAMPERED,
        CaptureRetentionPhase.DELETED,
    },
    CaptureRetentionPhase.DELETING: {
        CaptureRetentionPhase.DELETING,
        CaptureRetentionPhase.TAMPERED,
        CaptureRetentionPhase.DELETED,
    },
    CaptureRetentionPhase.TAMPERED: {CaptureRetentionPhase.TAMPERED},
    CaptureRetentionPhase.DELETED: {CaptureRetentionPhase.DELETED},
}


def is_state_successor(previous: CaptureState, candidate: CaptureState) -> bool:
    return (
        type(previous) is CaptureState
        and type(candidate) is CaptureState
        and candidate in _STATE_SUCCESSORS[previous]
    )


def is_reference_successor(
    previous: CaptureReferenceStatus,
    candidate: CaptureReferenceStatus,
    *,
    allow_same: bool = True,
) -> bool:
    return (
        type(previous) is CaptureReferenceStatus
        and type(candidate) is CaptureReferenceStatus
        and (allow_same or candidate is not previous)
        and candidate in _REFERENCE_SUCCESSORS[previous]
    )


def is_delivery_successor(
    previous: CaptureDeliveryStatus,
    candidate: CaptureDeliveryStatus,
    *,
    allow_same: bool = True,
) -> bool:
    return (
        type(previous) is CaptureDeliveryStatus
        and type(candidate) is CaptureDeliveryStatus
        and (allow_same or candidate is not previous)
        and candidate in _DELIVERY_SUCCESSORS[previous]
    )


def is_retention_successor(
    previous: CaptureRetentionPhase,
    candidate: CaptureRetentionPhase,
) -> bool:
    return (
        type(previous) is CaptureRetentionPhase
        and type(candidate) is CaptureRetentionPhase
        and candidate in _RETENTION_SUCCESSORS[previous]
    )


# ---------------------------------------------------------------------------
# Reclaimability declarations — B-I1 (workstream B)
# ---------------------------------------------------------------------------

# The canonical sweep-grace constant.  Formerly ``_RETENTION_SECONDS`` in
# ``_capture_lifecycle.py``; moved here so that the declaration and the
# enforcement share one value.
SWEEP_GRACE_SECONDS: float = 3600.0


class ReclaimKind(StrEnum):
    """How a state's ledger bytes are eventually freed."""

    SWEEP_AFTER_GRACE = "SWEEP_AFTER_GRACE"  # reclaimable after a finite grace
    TOMBSTONE = "TOMBSTONE"  # bounded by max_tombstones in compaction
    FORENSIC_HOLD = "FORENSIC_HOLD"  # held for finite forensic window


class StateReclaimabilityDef(NamedTuple):
    """One entry in the total state-reclaimability registry."""

    state: CaptureState
    kind: ReclaimKind
    duration_seconds: float | None
    rationale: str


STATE_RECLAIMABILITY: dict[CaptureState, StateReclaimabilityDef] = {
    CaptureState.RESERVED: StateReclaimabilityDef(
        state=CaptureState.RESERVED,
        kind=ReclaimKind.SWEEP_AFTER_GRACE,
        duration_seconds=SWEEP_GRACE_SECONDS,
        rationale="abandoned reservation — reclaimable after standard grace",
    ),
    CaptureState.STAGED: StateReclaimabilityDef(
        state=CaptureState.STAGED,
        kind=ReclaimKind.SWEEP_AFTER_GRACE,
        duration_seconds=SWEEP_GRACE_SECONDS,
        rationale="staging failure — reclaimable after standard grace",
    ),
    CaptureState.PUBLISHED_WRITING: StateReclaimabilityDef(
        state=CaptureState.PUBLISHED_WRITING,
        kind=ReclaimKind.SWEEP_AFTER_GRACE,
        duration_seconds=SWEEP_GRACE_SECONDS,
        rationale="incomplete publication — reclaimable after standard grace",
    ),
    CaptureState.FINALIZED: StateReclaimabilityDef(
        state=CaptureState.FINALIZED,
        kind=ReclaimKind.SWEEP_AFTER_GRACE,
        duration_seconds=SWEEP_GRACE_SECONDS,
        # Coupling invariant: FINALIZED's sweep grace must never be set below
        # the replay-reference window (_REFERENCE_LIFETIME_SECONDS = 1800s in
        # _capture_lifecycle.py), or sweeps would delete artifacts whose issued
        # references are still valid.
        rationale="completed capture — reclaimable after retention window",
    ),
    CaptureState.FAILED: StateReclaimabilityDef(
        state=CaptureState.FAILED,
        kind=ReclaimKind.SWEEP_AFTER_GRACE,
        duration_seconds=SWEEP_GRACE_SECONDS,
        rationale="failed capture — reclaimable after forensic retention",
    ),
    CaptureState.ABANDONED: StateReclaimabilityDef(
        state=CaptureState.ABANDONED,
        kind=ReclaimKind.SWEEP_AFTER_GRACE,
        duration_seconds=SWEEP_GRACE_SECONDS,
        rationale="abandoned capture — reclaimable after standard grace",
    ),
    CaptureState.DELETING: StateReclaimabilityDef(
        state=CaptureState.DELETING,
        kind=ReclaimKind.SWEEP_AFTER_GRACE,
        duration_seconds=0.0,
        rationale="deletion in progress — immediately reclaimable",
    ),
    CaptureState.TAMPERED: StateReclaimabilityDef(
        state=CaptureState.TAMPERED,
        kind=ReclaimKind.FORENSIC_HOLD,
        duration_seconds=86400.0,
        # 24h forensic hold: long enough for post-incident investigation,
        # finite so the budget ratchet is bounded.
        rationale="tampered evidence — held for 24h forensic window then reclaimable",
    ),
    CaptureState.DELETED: StateReclaimabilityDef(
        state=CaptureState.DELETED,
        kind=ReclaimKind.TOMBSTONE,
        duration_seconds=None,
        rationale="tombstone — bounded by max_tombstones in compaction",
    ),
}

# Import-time totality assertion.
if set(STATE_RECLAIMABILITY) != set(CaptureState):
    raise AssertionError(
        "STATE_RECLAIMABILITY must cover exactly the CaptureState members: "
        f"missing={set(CaptureState) - set(STATE_RECLAIMABILITY)}, "
        f"extra={set(STATE_RECLAIMABILITY) - set(CaptureState)}"
    )
for _key, _entry in STATE_RECLAIMABILITY.items():
    if _key != _entry.state:
        raise AssertionError(
            f"STATE_RECLAIMABILITY key {_key!r} does not match entry state {_entry.state!r}"
        )
    if not _entry.rationale:
        raise AssertionError(f"STATE_RECLAIMABILITY[{_key!r}].rationale is empty")
    if _entry.kind is ReclaimKind.SWEEP_AFTER_GRACE:
        if not isinstance(_entry.duration_seconds, (int, float)) or _entry.duration_seconds < 0:
            raise AssertionError(
                f"STATE_RECLAIMABILITY[{_key!r}] SWEEP_AFTER_GRACE requires non-negative duration"
            )
    elif _entry.kind is ReclaimKind.TOMBSTONE:
        if _entry.duration_seconds is not None:
            raise AssertionError(
                f"STATE_RECLAIMABILITY[{_key!r}] TOMBSTONE must have duration_seconds=None"
            )
    elif _entry.kind is ReclaimKind.FORENSIC_HOLD:
        if not isinstance(_entry.duration_seconds, (int, float)) or _entry.duration_seconds <= 0:
            raise AssertionError(
                f"STATE_RECLAIMABILITY[{_key!r}] FORENSIC_HOLD requires positive duration"
            )
