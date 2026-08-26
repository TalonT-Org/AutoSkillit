"""Ledger fault points and accounting-status authorities.

Owns the :class:`_LedgerFaultPoint` enum, the ``_ignore_fault`` callback
default, and the ``_accounting_status`` / ``_uninitialized_stream_result``
value-constructors. The busy/recovery SQL error-code masks, the rollback
helper, the ``_sqlite_primary_code`` classifier, and the ``_LedgerContended``
exception live in :mod:`._sqlite_errors` to break the cross-shard lazy-import
cycle.

Wavefront 1 of #4667.
"""

from __future__ import annotations

from enum import StrEnum

from autoskillit.core import (
    AdmissionDecision,
    AdmissionDecisionKind,
    AdmissionTransition,
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionStreamKey,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    RequestReconciliationEvent,
)

from ._codec import _zero_state


class _LedgerFaultPoint(StrEnum):
    BEFORE_REDUCTION = "before_reduction"
    AFTER_REDUCTION = "after_reduction"
    AFTER_JOURNAL = "after_journal"
    DURING_EFFECTS = "during_effects"
    AFTER_STATE_SHADOW = "after_state_shadow"
    BEFORE_COMMIT = "before_commit"
    AFTER_COMMIT = "after_commit"


def _ignore_fault(fault_point: _LedgerFaultPoint) -> None:
    del fault_point


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


__all__ = [
    "_LedgerFaultPoint",
    "_ignore_fault",
    "_accounting_status",
    "_uninitialized_stream_result",
]
