"""Lifecycle-linearized entrances to verified shell-capture readers."""

from __future__ import annotations

import errno
import fcntl
import os
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from . import _cleanup, _descriptor, _ledger, _reader, _snapshot

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
        _cleanup.close_preserving_primary(
            lease_fd,
            exc,
            context="writer lease descriptor cleanup",
        )
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            raise lifecycle_error("writer lease unexpectedly contended") from exc
        raise lifecycle_error("writer lease capability unavailable") from exc


def _open_linearized_reader(
    store: _LifecycleStore,
    *,
    capture_id: str,
    resolve_manifest: Callable[
        [_ledger.CaptureLifecycleRecord | None],
        _snapshot.CaptureFinalManifest,
    ],
    acquire_descriptor: Callable[[_snapshot.CaptureFinalManifest], int],
    lifecycle_error: type[Exception],
    changed_message: str,
) -> _reader.VerifiedCaptureReader:
    fd = -1
    try:
        with store._locked():
            records, _epoch, _size = store._load_locked()
            record = records.get(capture_id)
            manifest = resolve_manifest(record)
            fd = acquire_descriptor(manifest)
            _descriptor.inspect_capture_descriptor(
                fd,
                manifest,
                error_type=_snapshot.CaptureAuthorityError,
            )
            revision = record.revision if record is not None else -1
            manifest_bytes = record.manifest_bytes if record is not None else b""

        _snapshot.verify_capture_descriptor(fd, manifest)

        with store._locked():
            records, _epoch, _size = store._load_locked()
            current = records.get(capture_id)
            current_manifest = resolve_manifest(current)
            if (
                current is None
                or current.revision != revision
                or current.manifest_bytes != manifest_bytes
                or current_manifest != manifest
            ):
                raise lifecycle_error(changed_message)
        reader = _reader._make_verified_reader(fd, manifest, revision)
        fd = -1
        return reader
    except BaseException as primary_error:
        if fd >= 0:
            _cleanup.close_preserving_primary(
                fd,
                primary_error,
                context="verified capture descriptor cleanup",
            )
        raise


def open_verified_capture(
    store: _LifecycleStore,
    token: str,
    *,
    lifecycle_error: type[Exception],
) -> _reader.VerifiedCaptureReader:
    """Resolve a published reference under a retained shared carrier lease."""

    hint = _snapshot.parse_capture_reference(token)

    def resolve_manifest(
        record: _ledger.CaptureLifecycleRecord | None,
    ) -> _snapshot.CaptureFinalManifest:
        return _published_manifest(
            store,
            record,
            token,
            capture_id=hint.capture_id,
            incarnation=hint.incarnation,
            lifecycle_error=lifecycle_error,
        )

    def acquire_descriptor(manifest: _snapshot.CaptureFinalManifest) -> int:
        try:
            fd = os.open(manifest.carrier_name, _READ_FLAGS, dir_fd=store._root_fd)
        except OSError as exc:
            raise lifecycle_error("capture carrier is unavailable") from exc
        try:
            _acquire_shared_lease(fd, lifecycle_error)
            return fd
        except BaseException as primary_error:
            _cleanup.close_preserving_primary(
                fd,
                primary_error,
                context="shared lease descriptor cleanup",
            )
            raise

    return _open_linearized_reader(
        store,
        capture_id=hint.capture_id,
        resolve_manifest=resolve_manifest,
        acquire_descriptor=acquire_descriptor,
        lifecycle_error=lifecycle_error,
        changed_message="verified capture changed during resolution",
    )


def adopt_verified_capture(
    store: _LifecycleStore,
    finalized: _snapshot.FinalizedCapture,
    fd: int,
    *,
    lifecycle_error: type[Exception],
) -> _reader.VerifiedCaptureReader:
    """Adopt a producer's already-exclusive carrier open file description."""

    if type(finalized) is not _snapshot.FinalizedCapture:
        try:
            raise lifecycle_error("producer entrance requires finalized capture")
        except BaseException as primary_error:
            _cleanup.close_preserving_primary(
                fd,
                primary_error,
                context="capture carrier cleanup",
            )
            raise
    manifest = finalized.snapshot.manifest

    def resolve_manifest(
        record: _ledger.CaptureLifecycleRecord | None,
    ) -> _snapshot.CaptureFinalManifest:
        return _producer_manifest(
            store,
            record,
            finalized,
            lifecycle_error=lifecycle_error,
        )

    return _open_linearized_reader(
        store,
        capture_id=manifest.capture_id,
        resolve_manifest=resolve_manifest,
        acquire_descriptor=lambda _manifest: fd,
        lifecycle_error=lifecycle_error,
        changed_message="producer capture changed during verification",
    )
