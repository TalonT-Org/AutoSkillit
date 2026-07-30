"""Transactional reference publication and hook-stdout delivery state."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol, TypeAlias

from . import _ledger, _snapshot, _sweep

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
RuntimeErrors: TypeAlias = tuple[type[Exception], ...]

_REFERENCE_TRANSITIONS = {
    _ledger.CaptureReferenceStatus.ISSUED: {
        _ledger.CaptureReferenceStatus.PUBLISHED,
        _ledger.CaptureReferenceStatus.UNAVAILABLE,
        _ledger.CaptureReferenceStatus.UNKNOWN,
        _ledger.CaptureReferenceStatus.EXPIRED,
        _ledger.CaptureReferenceStatus.REVOKED,
    },
    _ledger.CaptureReferenceStatus.PUBLISHED: {
        _ledger.CaptureReferenceStatus.UNAVAILABLE,
        _ledger.CaptureReferenceStatus.UNKNOWN,
        _ledger.CaptureReferenceStatus.EXPIRED,
        _ledger.CaptureReferenceStatus.REVOKED,
    },
    _ledger.CaptureReferenceStatus.UNAVAILABLE: {
        _ledger.CaptureReferenceStatus.EXPIRED,
        _ledger.CaptureReferenceStatus.REVOKED,
    },
    _ledger.CaptureReferenceStatus.UNKNOWN: {
        _ledger.CaptureReferenceStatus.EXPIRED,
        _ledger.CaptureReferenceStatus.REVOKED,
    },
}

_DELIVERY_TRANSITIONS = {
    _ledger.CaptureDeliveryStatus.NOT_ATTEMPTED: {
        _ledger.CaptureDeliveryStatus.ATTEMPTING,
        _ledger.CaptureDeliveryStatus.UNKNOWN,
    },
    _ledger.CaptureDeliveryStatus.ATTEMPTING: {
        _ledger.CaptureDeliveryStatus.DELIVERED,
        _ledger.CaptureDeliveryStatus.FAILED,
        _ledger.CaptureDeliveryStatus.UNKNOWN,
    },
}


class _LifecycleStore(Protocol):
    def get_record(self, capture_id: str) -> _ledger.CaptureLifecycleRecord | None: ...

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
    ) -> _ledger.CaptureLifecycleRecord: ...

    def mark_delivery_unknown(
        self,
        value: DeliveryValue,
    ) -> _ledger.CaptureLifecycleRecord: ...

    def _transition_current(
        self,
        capture_id: str,
        incarnation: str,
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
    if target not in _REFERENCE_TRANSITIONS.get(expected, set()):
        raise lifecycle_error("capture reference transition is not allowed")
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
    store._transition_current(
        authority.capture_id,
        authority.incarnation,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=lambda record: _reference_transition_for_manifest(
            record,
            manifest=manifest,
            expected=_ledger.CaptureReferenceStatus.ISSUED,
            target=_ledger.CaptureReferenceStatus.PUBLISHED,
            lifecycle_error=lifecycle_error,
        ),
    )
    return _snapshot._make_published_reference(issuance)


def _reference_transition_for_manifest(
    record: _ledger.CaptureLifecycleRecord,
    *,
    manifest: _snapshot.CaptureFinalManifest,
    expected: _ledger.CaptureReferenceStatus,
    target: _ledger.CaptureReferenceStatus,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    if record.manifest != manifest:
        raise lifecycle_error("finalized capture record is unavailable")
    return _reference_transition(
        record,
        expected=expected,
        target=target,
        lifecycle_error=lifecycle_error,
    )


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
    manifest = snapshot.manifest

    def transform(
        current: _ledger.CaptureLifecycleRecord,
    ) -> _ledger.CaptureLifecycleRecord:
        if current.reference_status not in {
            _ledger.CaptureReferenceStatus.ISSUED,
            _ledger.CaptureReferenceStatus.PUBLISHED,
        }:
            raise lifecycle_error("capture reference cannot become unavailable")
        return _reference_transition_for_manifest(
            current,
            manifest=manifest,
            expected=current.reference_status,
            target=_ledger.CaptureReferenceStatus.UNAVAILABLE,
            lifecycle_error=lifecycle_error,
        )

    store._transition_current(
        manifest.capture_id,
        manifest.incarnation,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=transform,
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
    manifest = finalized.snapshot.manifest

    def transform(
        current: _ledger.CaptureLifecycleRecord,
    ) -> _ledger.CaptureLifecycleRecord:
        if current.reference_status not in {
            _ledger.CaptureReferenceStatus.ISSUED,
            _ledger.CaptureReferenceStatus.PUBLISHED,
        }:
            raise lifecycle_error("capture reference cannot become unknown")
        return _reference_transition_for_manifest(
            current,
            manifest=manifest,
            expected=current.reference_status,
            target=_ledger.CaptureReferenceStatus.UNKNOWN,
            lifecycle_error=lifecycle_error,
        )

    return store._transition_current(
        manifest.capture_id,
        manifest.incarnation,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=transform,
    )


def revoke_reference(
    store: _LifecycleStore,
    finalized: _snapshot.FinalizedCapture,
    *,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    if type(finalized) is not _snapshot.FinalizedCapture or finalized.issuance is None:
        raise lifecycle_error("revocation requires an issued finalized capture")
    manifest = finalized.snapshot.manifest

    def transform(
        current: _ledger.CaptureLifecycleRecord,
    ) -> _ledger.CaptureLifecycleRecord:
        if current.manifest != manifest:
            raise lifecycle_error("finalized capture record is unavailable")
        return _reference_transition(
            current,
            expected=current.reference_status,
            target=_ledger.CaptureReferenceStatus.REVOKED,
            lifecycle_error=lifecycle_error,
        )

    return store._transition_current(
        manifest.capture_id,
        manifest.incarnation,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=transform,
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


def invalidate_lost_reference(
    store: _LifecycleStore,
    finalized: _snapshot.FinalizedCapture,
    *,
    reason_code: str,
    lifecycle_error: type[Exception],
    runtime_errors: RuntimeErrors,
) -> None:
    try:
        store.mark_reference_unavailable(
            finalized,
            reason_code=reason_code,
        )
        return
    except runtime_errors:
        pass
    try:
        record = store.get_record(finalized.snapshot.manifest.capture_id)
        if record is None or record.manifest != finalized.snapshot.manifest:
            return
        if record.reference_status in {
            _ledger.CaptureReferenceStatus.UNAVAILABLE,
            _ledger.CaptureReferenceStatus.EXPIRED,
            _ledger.CaptureReferenceStatus.REVOKED,
            _ledger.CaptureReferenceStatus.UNKNOWN,
        }:
            return
        if record.reference_status in {
            _ledger.CaptureReferenceStatus.ISSUED,
            _ledger.CaptureReferenceStatus.PUBLISHED,
        }:
            mark_reference_unknown(
                store,
                finalized,
                lifecycle_error=lifecycle_error,
            )
    except runtime_errors:
        pass


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


def _validate_delivery_record(
    record: _ledger.CaptureLifecycleRecord,
    value: DeliveryValue,
    *,
    lifecycle_error: type[Exception],
) -> None:
    snapshot = _delivery_snapshot(value, lifecycle_error=lifecycle_error)
    if record.state != _ledger.CaptureState.FINALIZED or record.manifest != snapshot.manifest:
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


def _delivery_transition(
    record: _ledger.CaptureLifecycleRecord,
    *,
    expected: _ledger.CaptureDeliveryStatus,
    target: _ledger.CaptureDeliveryStatus,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    if record.delivery_status != expected:
        raise lifecycle_error("capture delivery transition predecessor changed")
    if target not in _DELIVERY_TRANSITIONS.get(expected, set()):
        raise lifecycle_error("capture delivery transition is not allowed")
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
    snapshot = _delivery_snapshot(value, lifecycle_error=lifecycle_error)
    manifest = snapshot.manifest

    def transform(
        current: _ledger.CaptureLifecycleRecord,
    ) -> _ledger.CaptureLifecycleRecord:
        _validate_delivery_record(current, value, lifecycle_error=lifecycle_error)
        return _delivery_transition(
            current,
            expected=expected,
            target=target,
            lifecycle_error=lifecycle_error,
        )

    return store._transition_current(
        manifest.capture_id,
        manifest.incarnation,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=transform,
    )


def mark_delivery_unknown(
    store: _LifecycleStore,
    value: DeliveryValue,
    *,
    lifecycle_error: type[Exception],
) -> _ledger.CaptureLifecycleRecord:
    snapshot = _delivery_snapshot(value, lifecycle_error=lifecycle_error)
    manifest = snapshot.manifest

    def transform(
        current: _ledger.CaptureLifecycleRecord,
    ) -> _ledger.CaptureLifecycleRecord:
        _validate_delivery_record(current, value, lifecycle_error=lifecycle_error)
        if current.delivery_status == _ledger.CaptureDeliveryStatus.DELIVERED:
            raise lifecycle_error("delivered capture cannot become unknown")
        return _delivery_transition(
            current,
            expected=current.delivery_status,
            target=_ledger.CaptureDeliveryStatus.UNKNOWN,
            lifecycle_error=lifecycle_error,
        )

    return store._transition_current(
        manifest.capture_id,
        manifest.incarnation,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=transform,
    )


def delivery_status(
    store: _LifecycleStore,
    value: DeliveryValue,
) -> _ledger.CaptureDeliveryStatus | None:
    record = store.get_record(value.snapshot.manifest.capture_id)
    if record is None or record.manifest != value.snapshot.manifest:
        return None
    return record.delivery_status


def transition_delivery_checked(
    store: _LifecycleStore,
    value: DeliveryValue,
    *,
    expected: _ledger.CaptureDeliveryStatus,
    target: _ledger.CaptureDeliveryStatus,
    lifecycle_error: type[Exception],
    runtime_errors: RuntimeErrors,
) -> None:
    try:
        store.transition_delivery(
            value,
            expected=expected,
            target=target,
        )
    except _ledger.CaptureTransitionCommittedError:
        if delivery_status(store, value) == target:
            return
        raise


def record_delivery_failure(
    store: _LifecycleStore,
    value: DeliveryValue,
    *,
    lifecycle_error: type[Exception],
    runtime_errors: RuntimeErrors,
) -> None:
    try:
        transition_delivery_checked(
            store,
            value,
            expected=_ledger.CaptureDeliveryStatus.ATTEMPTING,
            target=_ledger.CaptureDeliveryStatus.FAILED,
            lifecycle_error=lifecycle_error,
            runtime_errors=runtime_errors,
        )
    except runtime_errors:
        try:
            if delivery_status(store, value) is _ledger.CaptureDeliveryStatus.ATTEMPTING:
                store.mark_delivery_unknown(value)
        except runtime_errors:
            pass


def settle_finalized_failure(
    store: _LifecycleStore,
    finalized: _snapshot.FinalizedCapture,
    value: DeliveryValue | None,
    *,
    delivery_attempting: bool,
    delivery_bytes_flushed: bool,
    lifecycle_error: type[Exception],
    runtime_errors: RuntimeErrors,
) -> None:
    if value is not None:
        try:
            status = delivery_status(store, value)
        except runtime_errors:
            status = None
        if delivery_attempting or status is _ledger.CaptureDeliveryStatus.ATTEMPTING:
            if delivery_bytes_flushed:
                try:
                    store.mark_delivery_unknown(value)
                except runtime_errors:
                    pass
            else:
                record_delivery_failure(
                    store,
                    value,
                    lifecycle_error=lifecycle_error,
                    runtime_errors=runtime_errors,
                )
            return
        if status not in {None, _ledger.CaptureDeliveryStatus.NOT_ATTEMPTED}:
            return
    if finalized.issuance is not None:
        invalidate_lost_reference(
            store,
            finalized,
            reason_code="PRE_DELIVERY_REFERENCE_LOST",
            lifecycle_error=lifecycle_error,
            runtime_errors=runtime_errors,
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


def normalize_interrupted_deliveries(
    store: Any,
    *,
    lifecycle_error: type[Exception],
    lease_live: type[Exception],
    tampered: type[Exception],
) -> None:
    with store._locked():
        records, _compaction_epoch, _size = store._load_locked()
        candidates = [
            record
            for record in records.values()
            if record.state is _ledger.CaptureState.FINALIZED
            and (
                record.delivery_status is _ledger.CaptureDeliveryStatus.ATTEMPTING
                or (
                    record.delivery_status is _ledger.CaptureDeliveryStatus.NOT_ATTEMPTED
                    and record.reference_status
                    in {
                        _ledger.CaptureReferenceStatus.ISSUED,
                        _ledger.CaptureReferenceStatus.PUBLISHED,
                    }
                )
            )
        ]
    for expected in sorted(candidates, key=lambda record: record.capture_id):
        lease = None
        try:
            lease = store._acquire_cleanup_lease(expected)
        except (lease_live, tampered, lifecycle_error, OSError):
            continue
        try:
            with store._locked():
                records, compaction_epoch, size = store._load_locked()
                current = records.get(expected.capture_id)
                if not _sweep._same_record(expected, current):
                    continue
                candidate = _restart_transition(
                    current,
                    lifecycle_error=lifecycle_error,
                )
                store._transition_locked(
                    records=records,
                    compaction_epoch=compaction_epoch,
                    ledger_size=size,
                    authority=store._authority_for(current),
                    allowed_states={_ledger.CaptureState.FINALIZED},
                    transform=lambda _record: candidate,
                )
        finally:
            if lease is not None:
                os.close(lease.fd)


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
    return store._transition_current(
        record.capture_id,
        record.incarnation,
        allowed_states={_ledger.CaptureState.FINALIZED},
        transform=lambda current: _restart_transition(
            current,
            lifecycle_error=lifecycle_error,
        ),
    )
