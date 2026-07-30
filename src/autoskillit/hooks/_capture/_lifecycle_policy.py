"""Canonical lifecycle status enums and successor policy."""

from __future__ import annotations

import sys
from enum import StrEnum

_THIS_MODULE = sys.modules[__name__]
for _alias in (
    "_capture._lifecycle_policy",
    "autoskillit.hooks._capture._lifecycle_policy",
):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture lifecycle policy module identity")

__all__ = [
    "CaptureDeliveryStatus",
    "CaptureReferenceStatus",
    "CaptureRetentionPhase",
    "CaptureSnapshotStatus",
    "CaptureState",
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
    CaptureState.TAMPERED: {CaptureState.TAMPERED},
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
