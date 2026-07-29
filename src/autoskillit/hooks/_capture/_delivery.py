"""Transactional reference publication and hook-stdout delivery state."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol, TypeAlias

from . import _ledger, _snapshot

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._delivery", "autoskillit.hooks._capture._delivery"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture delivery module identity")

DeliveryValue: TypeAlias = (
    _snapshot.FinalizedCapture
    | _snapshot.PublishedCaptureReference
    | _snapshot.UnavailableCaptureReference
)


class _LifecycleStore(Protocol):
    def get_record(self, capture_id: str) -> _ledger.CaptureLifecycleRecord | None: ...

    def _authority_for(
        self,
        record: _ledger.CaptureLifecycleRecord,
    ) -> _snapshot.CaptureWriteAuthority: ...

    def _transition(
        self,
        authority: _snapshot.CaptureWriteAuthority,
        *,
        allowed_states: set[_ledger.CaptureState],
        transform: Callable[
            [_ledger.CaptureLifecycleRecord],
            _ledger.CaptureLifecycleRecord,
        ],
    ) -> _ledger.CaptureLifecycleRecord: ...


def _reference_transition(
    record: _ledger.CaptureLifecycleRecord,
    *,
    expected: _ledger.CaptureReferenceStatus,
    target: _ledger.CaptureReferenceStatus,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    if record.reference_status != expected:
        raise lifecycle_error("capture reference transition predecessor changed")
    return replace(
        record,
        reference_status=target,
        revision=record.revision + 1,
    )


def publish_reference(
    store: _LifecycleStore,
    finalized: _snapshot.FinalizedCapture,
    *,
    lifecycle_error: type[Exception],
) -> _snapshot.PublishedCaptureReference:
    if type(finalized) is not _snapshot.FinalizedCapture or finalized.issuance is None:
        raise lifecycle_error("publication requires an issued finalized capture")
    issuance = finalized.issuance
    manifest = issuance.snapshot.manifest
    authority = _snapshot._make_write_authority(
        manifest.capture_id,
        manifest.incarnation,
        finalized.finalized_at_revision,
    )
    store._transition(
        authority,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=lambda record: _reference_transition(
            record,
            expected=_ledger.CaptureReferenceStatus.ISSUED,
            target=_ledger.CaptureReferenceStatus.PUBLISHED,
            lifecycle_error=lifecycle_error,
        ),
    )
    return _snapshot._make_published_reference(issuance)


def mark_reference_unavailable(
    store: _LifecycleStore,
    finalized: _snapshot.FinalizedCapture,
    *,
    reason_code: str,
    lifecycle_error: type[Exception],
) -> _snapshot.UnavailableCaptureReference:
    if type(finalized) is not _snapshot.FinalizedCapture or finalized.issuance is None:
        raise lifecycle_error("unavailable transition requires issued finalized capture")
    snapshot = finalized.snapshot
    record = store.get_record(snapshot.manifest.capture_id)
    if record is None or record.manifest != snapshot.manifest:
        raise lifecycle_error("finalized capture record is unavailable")
    authority = store._authority_for(record)
    store._transition(
        authority,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=lambda current: _reference_transition(
            current,
            expected=_ledger.CaptureReferenceStatus.ISSUED,
            target=_ledger.CaptureReferenceStatus.UNAVAILABLE,
            lifecycle_error=lifecycle_error,
        ),
    )
    return _snapshot._make_unavailable_reference(snapshot, reason_code)


def mark_reference_unknown(
    store: _LifecycleStore,
    finalized: _snapshot.FinalizedCapture,
    *,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    if type(finalized) is not _snapshot.FinalizedCapture or finalized.issuance is None:
        raise lifecycle_error("unknown transition requires issued finalized capture")
    record = store.get_record(finalized.snapshot.manifest.capture_id)
    if record is None or record.manifest != finalized.snapshot.manifest:
        raise lifecycle_error("finalized capture record is unavailable")
    authority = store._authority_for(record)
    return store._transition(
        authority,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=lambda current: replace(
            current,
            reference_status=_ledger.CaptureReferenceStatus.UNKNOWN,
            revision=current.revision + 1,
        ),
    )


def reference_result(
    finalized: _snapshot.FinalizedCapture,
    record: _ledger.CaptureLifecycleRecord | None,
    *,
    unavailable_reason: str,
    lifecycle_error: type[Exception],
) -> _snapshot.PublishedCaptureReference | _snapshot.UnavailableCaptureReference | None:
    if (
        type(finalized) is not _snapshot.FinalizedCapture
        or finalized.issuance is None
        or record is None
        or record.manifest != finalized.snapshot.manifest
    ):
        raise lifecycle_error("capture reference state cannot be reconciled")
    if record.reference_status == _ledger.CaptureReferenceStatus.PUBLISHED:
        return _snapshot._make_published_reference(finalized.issuance)
    if record.reference_status == _ledger.CaptureReferenceStatus.UNAVAILABLE:
        return _snapshot._make_unavailable_reference(
            finalized.snapshot,
            unavailable_reason,
        )
    if record.reference_status in {
        _ledger.CaptureReferenceStatus.ISSUED,
        _ledger.CaptureReferenceStatus.UNKNOWN,
    }:
        return None
    raise lifecycle_error("capture reference state cannot be reconciled")


def _delivery_snapshot(
    value: DeliveryValue,
    *,
    lifecycle_error: type[Exception],
) -> _snapshot.VerifiedCaptureSnapshot:
    if type(value) is _snapshot.FinalizedCapture:
        if value.issuance is not None:
            raise lifecycle_error("issued capture requires a publication result")
        return value.snapshot
    if type(value) in {
        _snapshot.PublishedCaptureReference,
        _snapshot.UnavailableCaptureReference,
    }:
        return value.snapshot
    raise lifecycle_error("invalid capture delivery value")


def _delivery_record(
    store: _LifecycleStore,
    value: DeliveryValue,
    *,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    snapshot = _delivery_snapshot(value, lifecycle_error=lifecycle_error)
    record = store.get_record(snapshot.manifest.capture_id)
    if (
        record is None
        or record.state != _ledger.CaptureState.FINALIZED
        or record.manifest != snapshot.manifest
    ):
        raise lifecycle_error("capture delivery authority is unavailable")
    if type(value) is _snapshot.FinalizedCapture:
        expected = _ledger.CaptureReferenceStatus.NOT_REQUESTED
    elif type(value) is _snapshot.PublishedCaptureReference:
        expected = _ledger.CaptureReferenceStatus.PUBLISHED
        if not _snapshot._reference_matches(value.token, snapshot.manifest):
            raise lifecycle_error("published capture reference does not match")
    else:
        expected = _ledger.CaptureReferenceStatus.UNAVAILABLE
    if record.reference_status != expected:
        raise lifecycle_error("capture delivery reference state changed")
    return record


def _delivery_transition(
    record: _ledger.CaptureLifecycleRecord,
    *,
    expected: _ledger.CaptureDeliveryStatus,
    target: _ledger.CaptureDeliveryStatus,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    if record.delivery_status != expected:
        raise lifecycle_error("capture delivery transition predecessor changed")
    return replace(
        record,
        delivery_status=target,
        revision=record.revision + 1,
    )


def transition_delivery(
    store: _LifecycleStore,
    value: DeliveryValue,
    *,
    expected: _ledger.CaptureDeliveryStatus,
    target: _ledger.CaptureDeliveryStatus,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    record = _delivery_record(store, value, lifecycle_error=lifecycle_error)
    return store._transition(
        store._authority_for(record),
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=lambda current: _delivery_transition(
            current,
            expected=expected,
            target=target,
            lifecycle_error=lifecycle_error,
        ),
    )


def mark_delivery_unknown(
    store: _LifecycleStore,
    value: DeliveryValue,
    *,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    record = _delivery_record(store, value, lifecycle_error=lifecycle_error)
    if record.delivery_status == _ledger.CaptureDeliveryStatus.DELIVERED:
        raise lifecycle_error("delivered capture cannot become unknown")
    return store._transition(
        store._authority_for(record),
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=lambda current: replace(
            current,
            delivery_status=_ledger.CaptureDeliveryStatus.UNKNOWN,
            revision=current.revision + 1,
        ),
    )


def _restart_transition(
    record: _ledger.CaptureLifecycleRecord,
    *,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    if record.delivery_status == _ledger.CaptureDeliveryStatus.ATTEMPTING:
        return replace(
            record,
            delivery_status=_ledger.CaptureDeliveryStatus.UNKNOWN,
            revision=record.revision + 1,
        )
    if (
        record.delivery_status == _ledger.CaptureDeliveryStatus.NOT_ATTEMPTED
        and record.reference_status
        in {
            _ledger.CaptureReferenceStatus.ISSUED,
            _ledger.CaptureReferenceStatus.PUBLISHED,
        }
    ):
        return replace(
            record,
            reference_status=_ledger.CaptureReferenceStatus.UNAVAILABLE,
            revision=record.revision + 1,
        )
    raise lifecycle_error("capture restart predecessor changed")


def recover_interrupted_delivery(
    store: _LifecycleStore,
    capture_id: str,
    *,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    """Normalize durable state when the sole in-memory reference has been lost."""

    record = store.get_record(capture_id)
    if record is None or record.state != _ledger.CaptureState.FINALIZED or record.manifest is None:
        raise lifecycle_error("finalized capture recovery state is unavailable")
    should_transition = record.delivery_status == _ledger.CaptureDeliveryStatus.ATTEMPTING or (
        record.delivery_status == _ledger.CaptureDeliveryStatus.NOT_ATTEMPTED
        and record.reference_status
        in {
            _ledger.CaptureReferenceStatus.ISSUED,
            _ledger.CaptureReferenceStatus.PUBLISHED,
        }
    )
    if not should_transition:
        return record
    return store._transition(
        store._authority_for(record),
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=lambda current: _restart_transition(
            current,
            lifecycle_error=lifecycle_error,
        ),
    )
