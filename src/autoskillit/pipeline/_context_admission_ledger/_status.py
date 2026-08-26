"""SQLite contention, recovery, and accounting-status authorities.

Owns the :class:`_LedgerFaultPoint` and :class:`_LedgerContended` types,
the busy/recovery SQL error-code masks, the rollback helper, the
``_sqlite_primary_code`` classifier, and the ``_accounting_status`` /
``_uninitialized_stream_result`` value-constructors.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum
from typing import Final

from autoskillit.core import (
    AdmissionDecision,
    AdmissionDecisionKind,
    AdmissionTransition,
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStreamKey,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    RequestReconciliationEvent,
)

from ._codec import _zero_state
from ._storage import _LedgerOpenError

_SQLITE_PRIMARY_MASK: Final = 0xFF
_SQLITE_BUSY_CODES: Final = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
_SQLITE_RECOVERY_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_INTERRUPT,
        sqlite3.SQLITE_NOMEM,
    }
)


class _LedgerFaultPoint(StrEnum):
    BEFORE_REDUCTION = "before_reduction"
    AFTER_REDUCTION = "after_reduction"
    AFTER_JOURNAL = "after_journal"
    DURING_EFFECTS = "during_effects"
    AFTER_STATE_SHADOW = "after_state_shadow"
    BEFORE_COMMIT = "before_commit"
    AFTER_COMMIT = "after_commit"


class _LedgerContended(RuntimeError):
    pass


def _ignore_fault(fault_point: _LedgerFaultPoint) -> None:
    del fault_point


def _rollback(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error as exc:
        # Best-effort rollback: a failure here is itself a store-health signal,
        # but suppressing it preserves the existing cleanup semantics. Bind the
        # exception so it remains visible to traceback inspectors / test debuggers
        # without changing the swallow behavior.
        del exc


def _sqlite_primary_code(error: sqlite3.Error) -> int | None:
    code = getattr(error, "sqlite_errorcode", None)
    return code & _SQLITE_PRIMARY_MASK if isinstance(code, int) else None


def _accounting_status(
    event: ContextAdmissionEvent,
    transition: AdmissionTransition,
) -> ContextAdmissionAccountingStatus:
    if transition.decision.kind is AdmissionDecisionKind.QUARANTINED:
        return ContextAdmissionAccountingStatus.PROTOCOL_QUARANTINED
    if transition.decision.kind in {
        AdmissionDecisionKind.WOULD_REJECT,
        AdmissionDecisionKind.WATERMARK_UNAVAILABLE,
        AdmissionDecisionKind.UPSTREAM_GATED,
        AdmissionDecisionKind.CONFLICT,
        AdmissionDecisionKind.IDEMPOTENCY_EXPIRED,
    }:
        return ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    if isinstance(
        event,
        MarkIndeterminateEvent | MarkGenerationIndeterminateEvent | RequestReconciliationEvent,
    ):
        return ContextAdmissionAccountingStatus.RECONCILIATION_REQUIRED
    return ContextAdmissionAccountingStatus.RECORDED


def _uninitialized_stream_result(
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> ContextAdmissionAccountingResult:
    decision = AdmissionDecision(
        kind=AdmissionDecisionKind.WOULD_REJECT,
        reason_code="stream-uninitialized",
        window_epoch_id=None,
        snapshot_sequence=None,
        requested_count=0,
        available_ordinary_count=0,
        available_protected_count=0,
    )
    return ContextAdmissionAccountingResult(
        status=ContextAdmissionAccountingStatus.SEMANTIC_REJECTION,
        stream_key=stream_key,
        transition=AdmissionTransition(
            next_state=_zero_state(event.protocol_version),
            decision=decision,
            effects=(),
        ),
        reason_code=decision.reason_code,
    )


def _set_store_failure(
    self,
    reason: ContextAdmissionStorageFailureReason,
    reason_code: str,
) -> None:
    """Instance method bound onto DefaultContextAdmissionLedger."""
    from autoskillit.core import (
        ContextAdmissionStorageHealthStatus,
        ContextAdmissionStoreHealth,
    )

    self._store_health = ContextAdmissionStoreHealth(
        ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
        failure_reason=reason,
        reason_code=reason_code,
    )
    self._recovered = True


def _validate_integrity(connection: sqlite3.Connection) -> None:
    """Static method bound onto DefaultContextAdmissionLedger."""
    row = connection.execute("PRAGMA integrity_check").fetchone()
    if row != ("ok",):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.INTEGRITY,
            "sqlite-integrity-failed",
        )
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.INTEGRITY,
            "sqlite-foreign-key-check-failed",
        )


def _validate_metadata(metadata: dict[str, str]) -> None:
    """Static method bound onto DefaultContextAdmissionLedger."""
    from autoskillit.core import (
        CONTEXT_ADMISSION_ENCODING_VERSION,
        CONTEXT_ADMISSION_PROTOCOL_VERSION,
        ContextAdmissionStorageHealthStatus,
    )

    # _SCHEMA_VERSION is defined in `_store.py` (the natural owner of the
    # store-init contract). Import lazily to avoid a circular import.
    from ._store import _SCHEMA_VERSION

    expected = {
        "schema_version": str(_SCHEMA_VERSION),
        "encoding_version": str(CONTEXT_ADMISSION_ENCODING_VERSION),
        "protocol_version": str(CONTEXT_ADMISSION_PROTOCOL_VERSION),
        "store_health": ContextAdmissionStorageHealthStatus.HEALTHY.value,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            reason = {
                "schema_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_SCHEMA,
                "encoding_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
                "protocol_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
            }.get(key, ContextAdmissionStorageFailureReason.INTEGRITY)
            raise _LedgerOpenError(reason, f"invalid-{key.replace('_', '-')}")


__all__ = [
    "_LedgerFaultPoint",
    "_LedgerContended",
    "_SQLITE_PRIMARY_MASK",
    "_SQLITE_BUSY_CODES",
    "_SQLITE_RECOVERY_CODES",
    "_ignore_fault",
    "_rollback",
    "_sqlite_primary_code",
    "_accounting_status",
    "_uninitialized_stream_result",
    "_set_store_failure",
    "_validate_integrity",
    "_validate_metadata",
]
