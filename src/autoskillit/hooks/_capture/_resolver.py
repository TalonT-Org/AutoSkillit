"""Lifecycle-linearized entrances to verified shell-capture readers."""

from __future__ import annotations

import errno
import fcntl
import os
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from . import _descriptor, _ledger, _reader, _snapshot

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._resolver", "autoskillit.hooks._capture._resolver"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture resolver module identity")

_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


class _LifecycleStore(Protocol):
    _root_fd: int
    _project_identity: tuple[int, int]
    _root_identity: tuple[int, int]
    _wall_clock: Callable[[], float]

    def _locked(self, *, blocking: bool = True) -> AbstractContextManager[None]: ...

    def _load_locked(
        self,
    ) -> tuple[dict[str, _ledger.CaptureLifecycleRecord], int, int]: ...


def _record_manifest(
    store: _LifecycleStore,
    record: _ledger.CaptureLifecycleRecord | None,
    *,
    capture_id: str,
    incarnation: str,
    lifecycle_error: type[Exception],
) -> _snapshot.CaptureFinalManifest:
    if record is None:
        raise lifecycle_error("verified capture is unavailable")
    # Ledger projections may originate from either supported hook import spelling.
    if record.state != _ledger.CaptureState.FINALIZED:
        raise lifecycle_error("verified capture is not finalized")
    if record.capture_id != capture_id or record.incarnation != incarnation:
        raise lifecycle_error("verified capture incarnation does not match")
    if (
        record.project_identity != store._project_identity
        or record.root_identity != store._root_identity
    ):
        raise lifecycle_error("verified capture physical binding does not match")
    if type(record.manifest) is not _snapshot.CaptureFinalManifest:
        raise lifecycle_error("verified capture manifest is unavailable")
    if (
        record.manifest_bytes != _snapshot.encode_capture_final_manifest(record.manifest)
        or record.finalized_at_revision != record.manifest.finalized_at_revision
        or record.public_name != record.manifest.carrier_name
        or record.artifact_identity != record.manifest.carrier_identity
    ):
        raise lifecycle_error("verified capture manifest binding does not match")
    if record.retention_phase != _ledger.CaptureRetentionPhase.ACTIVE:
        raise lifecycle_error("verified capture retention is inactive")
    if store._wall_clock() >= record.manifest.retention_deadline:
        raise lifecycle_error("verified capture retention has expired")
    return record.manifest


def _published_manifest(
    store: _LifecycleStore,
    record: _ledger.CaptureLifecycleRecord | None,
    token: str,
    *,
    capture_id: str,
    incarnation: str,
    lifecycle_error: type[Exception],
) -> _snapshot.CaptureFinalManifest:
    manifest = _record_manifest(
        store,
        record,
        capture_id=capture_id,
        incarnation=incarnation,
        lifecycle_error=lifecycle_error,
    )
    if (
        record is None
        or record.reference_status != _ledger.CaptureReferenceStatus.PUBLISHED
        or manifest.reference_expiry is None
        or store._wall_clock() >= manifest.reference_expiry
        or not _snapshot._reference_matches(token, manifest)
    ):
        raise lifecycle_error("verified capture reference is unavailable")
    return manifest


def _producer_manifest(
    store: _LifecycleStore,
    record: _ledger.CaptureLifecycleRecord | None,
    finalized: _snapshot.FinalizedCapture,
    *,
    lifecycle_error: type[Exception],
) -> _snapshot.CaptureFinalManifest:
    expected = finalized.snapshot.manifest
    manifest = _record_manifest(
        store,
        record,
        capture_id=expected.capture_id,
        incarnation=expected.incarnation,
        lifecycle_error=lifecycle_error,
    )
    expected_status = (
        _ledger.CaptureReferenceStatus.ISSUED
        if finalized.issuance is not None
        else _ledger.CaptureReferenceStatus.NOT_REQUESTED
    )
    if (
        record is None
        or record.reference_status != expected_status
        or manifest != expected
        or record.revision != finalized.finalized_at_revision
    ):
        raise lifecycle_error("producer capture authority is stale")
    return manifest


def _acquire_shared_lease(fd: int, lifecycle_error: type[Exception]) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            raise lifecycle_error("capture carrier lease is contended") from exc
        raise lifecycle_error("capture carrier lease is unavailable") from exc


def acquire_writer_lease(
    artifact_fd: int,
    *,
    lifecycle_error: type[Exception],
) -> int:
    try:
        lease_fd = os.dup(artifact_fd)
    except OSError as exc:
        raise lifecycle_error("cannot duplicate writer lease descriptor") from exc
    try:
        fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lease_fd
    except OSError as exc:
        os.close(lease_fd)
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            raise lifecycle_error("writer lease unexpectedly contended") from exc
        raise lifecycle_error("writer lease capability unavailable") from exc


def open_verified_capture(
    store: _LifecycleStore,
    token: str,
    *,
    lifecycle_error: type[Exception],
) -> _reader.VerifiedCaptureReader:
    """Resolve a published reference under a retained shared carrier lease."""

    fd = -1
    try:
        with store._locked():
            hint = _snapshot.parse_capture_reference(token)
            records, _epoch, _size = store._load_locked()
            record = records.get(hint.capture_id)
            manifest = _published_manifest(
                store,
                record,
                token,
                capture_id=hint.capture_id,
                incarnation=hint.incarnation,
                lifecycle_error=lifecycle_error,
            )
            try:
                fd = os.open(manifest.carrier_name, _READ_FLAGS, dir_fd=store._root_fd)
            except OSError as exc:
                raise lifecycle_error("capture carrier is unavailable") from exc
            _descriptor.inspect_capture_descriptor(
                fd,
                manifest,
                error_type=_snapshot.CaptureAuthorityError,
            )
            _acquire_shared_lease(fd, lifecycle_error)
            revision = record.revision if record is not None else -1
            manifest_bytes = record.manifest_bytes if record is not None else b""

        _snapshot.verify_capture_descriptor(fd, manifest)

        with store._locked():
            records, _epoch, _size = store._load_locked()
            current = records.get(hint.capture_id)
            current_manifest = _published_manifest(
                store,
                current,
                token,
                capture_id=hint.capture_id,
                incarnation=hint.incarnation,
                lifecycle_error=lifecycle_error,
            )
            if (
                current is None
                or current.revision != revision
                or current.manifest_bytes != manifest_bytes
                or current_manifest != manifest
            ):
                raise lifecycle_error("verified capture changed during resolution")
        reader = _reader._make_verified_reader(fd, manifest, revision)
        fd = -1
        return reader
    finally:
        if fd >= 0:
            os.close(fd)


def adopt_verified_capture(
    store: _LifecycleStore,
    finalized: _snapshot.FinalizedCapture,
    fd: int,
    *,
    lifecycle_error: type[Exception],
) -> _reader.VerifiedCaptureReader:
    """Adopt a producer's already-exclusive carrier open file description."""

    try:
        if type(finalized) is not _snapshot.FinalizedCapture:
            raise lifecycle_error("producer entrance requires finalized capture")
        manifest = finalized.snapshot.manifest
        with store._locked():
            records, _epoch, _size = store._load_locked()
            record = records.get(manifest.capture_id)
            current_manifest = _producer_manifest(
                store,
                record,
                finalized,
                lifecycle_error=lifecycle_error,
            )
            _descriptor.inspect_capture_descriptor(
                fd,
                current_manifest,
                error_type=_snapshot.CaptureAuthorityError,
            )
            revision = record.revision if record is not None else -1
            manifest_bytes = record.manifest_bytes if record is not None else b""

        _snapshot.verify_capture_descriptor(fd, current_manifest)

        with store._locked():
            records, _epoch, _size = store._load_locked()
            current = records.get(manifest.capture_id)
            rechecked_manifest = _producer_manifest(
                store,
                current,
                finalized,
                lifecycle_error=lifecycle_error,
            )
            if (
                current is None
                or current.revision != revision
                or current.manifest_bytes != manifest_bytes
                or rechecked_manifest != current_manifest
            ):
                raise lifecycle_error("producer capture changed during verification")
        return _reader._make_verified_reader(fd, current_manifest, revision)
    except BaseException:
        os.close(fd)
        raise
