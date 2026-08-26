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
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreHealth,
    ContextAdmissionStreamKey,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    RequestReconciliationEvent,
    get_logger,
)

from ._codec import _zero_state  # noqa: E402

_logger = get_logger(__name__)

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
        # but suppressing it preserves the existing cleanup semantics. Surface
        # the cause via debug logging so it remains observable for diagnostics
        # without changing the swallow behavior.
        _logger.debug("context-admission rollback failed: %s", exc)


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
    self._store_health = ContextAdmissionStoreHealth(
        ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
        failure_reason=reason,
        reason_code=reason_code,
    )
    self._recovered = True


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
]
