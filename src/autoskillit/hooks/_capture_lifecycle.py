"""Descriptor-relative durable lifecycle for retained shell captures.

The module is a stdlib-only dependency leaf.  Callers provide an already-open
capture-root descriptor; pathname authority never enters this layer.
"""

from __future__ import annotations

import errno
import fcntl
import importlib
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _ledger as _capture_ledger
    from autoskillit.hooks._capture import _snapshot as _capture_snapshot
    from autoskillit.hooks._capture import _sweep as _capture_sweep
    from autoskillit.hooks._capture import _types as _capture_types
else:
    _CAPTURE_PACKAGE = f"{__package__}._capture" if __package__ else "_capture"
    _capture_ledger = importlib.import_module(f"{_CAPTURE_PACKAGE}._ledger")
    _capture_snapshot = importlib.import_module(f"{_CAPTURE_PACKAGE}._snapshot")
    _capture_sweep = importlib.import_module(f"{_CAPTURE_PACKAGE}._sweep")
    _capture_types = importlib.import_module(f"{_CAPTURE_PACKAGE}._types")

CaptureCleanupOutcome = _capture_types.CaptureCleanupOutcome
_ObservedArtifact = _capture_types.ObservedArtifact
_LockContended = _capture_types.LockContended
_Tampered = _capture_types.Tampered
_WriterLive = _capture_types.WriterLive

CaptureAuthorityError = _capture_snapshot.CaptureAuthorityError
CaptureFailureEvidence = _capture_snapshot.CaptureFailureEvidence
CaptureFinalManifest = _capture_snapshot.CaptureFinalManifest
CaptureWriteAuthority = _capture_snapshot.CaptureWriteAuthority
FinalizedCapture = _capture_snapshot.FinalizedCapture
IssuedCaptureReference = _capture_snapshot.IssuedCaptureReference
LegacyCleanupOnly = _capture_snapshot.LegacyCleanupOnly
PublishedCaptureReference = _capture_snapshot.PublishedCaptureReference
UnavailableCaptureReference = _capture_snapshot.UnavailableCaptureReference
VerifiedCaptureSnapshot = _capture_snapshot.VerifiedCaptureSnapshot

__all__ = [
    "CaptureCleanupOutcome",
    "CaptureDeliveryStatus",
    "CaptureLedgerError",
    "CaptureLifecycleError",
    "CaptureLifecycleRecord",
    "CaptureLifecycleStore",
    "CaptureReferenceStatus",
    "CaptureRetentionPhase",
    "CaptureState",
]

FRAME_MAGIC = _capture_ledger.FRAME_MAGIC
LEDGER_NAME = ".capture-lifecycle.ledger"
LOCK_NAME = ".capture-lifecycle.lock"
MAX_FRAME_BYTES = _capture_ledger.MAX_FRAME_BYTES
MAX_LEDGER_BYTES = _capture_ledger.MAX_LEDGER_BYTES
MAX_ACTIVE_RECORDS = 4096

_FORMAT_VERSION = _capture_ledger.CURRENT_FORMAT_VERSION
_RETENTION_SECONDS = 3600.0
_REFERENCE_LIFETIME_SECONDS = 1800.0
_MAX_RETRY_SECONDS = 3600.0
_COMPACTION_THRESHOLD_BYTES = 3 * 1024 * 1024
_MAX_COMPACTION_BYTES = 4 * 1024 * 1024
_MAX_TOMBSTONES = 256
_CAPTURE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_INCARNATION_RE = re.compile(r"^[0-9a-f]{32}$")
_PUBLIC_NAME_RE = re.compile(r"^shell_[0-9a-f]{16}\.log$")
_STAGING_NAME_RE = re.compile(r"^\.capture-staging-[0-9a-f]{16}-[0-9a-f]{16}$")
_QUARANTINE_NAME_RE = re.compile(r"^\.capture-quarantine-[0-9a-f]{16}-[0-9a-f]{16}$")
_CLOEXEC, _NOFOLLOW, _NONBLOCK = os.O_CLOEXEC, os.O_NOFOLLOW, os.O_NONBLOCK
_CONTROL_FLAGS = os.O_RDWR | os.O_CREAT | _CLOEXEC | _NOFOLLOW
_OBSERVE_FLAGS = os.O_RDWR | _CLOEXEC | _NOFOLLOW | _NONBLOCK
_ARTIFACT_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW
_UNTRUSTED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


class CaptureLifecycleError(RuntimeError):
    """Raised when lifecycle authority or state cannot be established safely."""


class CaptureLedgerError(CaptureLifecycleError):
    """Raised when durable lifecycle state is corrupt or exceeds a bound."""


CaptureState = _capture_ledger.CaptureState
CaptureReferenceStatus = _capture_ledger.CaptureReferenceStatus
CaptureDeliveryStatus = _capture_ledger.CaptureDeliveryStatus
CaptureRetentionPhase = _capture_ledger.CaptureRetentionPhase
CaptureLifecycleRecord = _capture_ledger.CaptureLifecycleRecord


def _identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _plain_int(value: object, *, minimum: int = 0) -> bool:
    return _capture_ledger._plain_int(value, minimum=minimum)


def _record_to_dict(record: CaptureLifecycleRecord) -> dict[str, object]:
    try:
        return _capture_ledger.record_to_dict(record)
    except _capture_ledger.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc


def _record_from_dict(value: object) -> CaptureLifecycleRecord:
    try:
        return _capture_ledger.record_from_dict(value)
    except _capture_ledger.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc


def _validate_record(record: CaptureLifecycleRecord) -> None:
    try:
        _capture_ledger.validate_record(record)
    except _capture_ledger.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc


def _legacy_record_from_dict(
    value: object,
    *,
    revision: int,
    compaction_epoch: int,
) -> CaptureLifecycleRecord:
    try:
        return _capture_ledger.legacy_record_from_dict(
            value,
            revision=revision,
            compaction_epoch=compaction_epoch,
        )
    except _capture_ledger.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc


def _validate_control_file(fd: int, name: str) -> None:
    value = os.fstat(fd)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or value.st_uid != os.geteuid()
        or value.st_mode & _UNTRUSTED_WRITE_BITS
    ):
        raise CaptureLifecycleError(f"unsafe lifecycle control file: {name}")


class CaptureLifecycleStore:
    """Root-bound lifecycle state, liveness, recovery, and deletion authority."""

    def __init__(
        self,
        root_fd: int,
        *,
        project_identity: tuple[int, int],
        root_identity: tuple[int, int],
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root_fd = root_fd
        self._project_identity = project_identity
        self._root_identity = root_identity
        self._wall_clock = wall_clock
        self._monotonic = monotonic

    @classmethod
    def from_open_authorities(
        cls,
        anchor: object,
        root: object,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> CaptureLifecycleStore:
        anchor_identity = getattr(anchor, "identity")
        root_identity = getattr(root, "identity")
        return cls(
            getattr(root, "fd"),
            project_identity=(anchor_identity.device, anchor_identity.inode),
            root_identity=(root_identity.device, root_identity.inode),
            wall_clock=wall_clock,
            monotonic=monotonic,
        )

    def _validate_root(self) -> None:
        if _identity(os.fstat(self._root_fd)) != self._root_identity:
            raise CaptureLifecycleError("capture root identity changed")

    @contextmanager
    def _locked(self, *, blocking: bool = True) -> Iterator[None]:
        self._validate_root()
        try:
            fd = os.open(LOCK_NAME, _CONTROL_FLAGS, 0o600, dir_fd=self._root_fd)
        except OSError as exc:
            raise CaptureLifecycleError("cannot open lifecycle lock") from exc
        try:
            _validate_control_file(fd, LOCK_NAME)
            try:
                operation = fcntl.LOCK_EX
                if not blocking:
                    operation |= fcntl.LOCK_NB
                fcntl.flock(fd, operation)
            except OSError as exc:
                if not blocking and exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise _LockContended from exc
                raise CaptureLifecycleError("cannot acquire lifecycle lock") from exc
            self._validate_root()
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _open_ledger(self) -> int:
        try:
            fd = os.open(LEDGER_NAME, _CONTROL_FLAGS | os.O_APPEND, 0o600, dir_fd=self._root_fd)
        except OSError as exc:
            raise CaptureLedgerError("cannot open lifecycle ledger") from exc
        try:
            _validate_control_file(fd, LEDGER_NAME)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _load_locked(self) -> tuple[dict[str, CaptureLifecycleRecord], int, int]:
        fd = self._open_ledger()
        try:
            size = os.fstat(fd).st_size
            if size > MAX_LEDGER_BYTES:
                raise CaptureLedgerError("lifecycle ledger exceeds bound")
            data = bytearray()
            offset = 0
            while offset < size:
                chunk = os.pread(fd, min(64 * 1024, size - offset), offset)
                if not chunk:
                    raise CaptureLedgerError("lifecycle ledger read ended early")
                data.extend(chunk)
                offset += len(chunk)
            try:
                decoded = _capture_ledger.decode_ledger(bytes(data))
            except _capture_ledger.LedgerCodecError as exc:
                raise CaptureLedgerError(str(exc)) from exc
            if decoded.truncate_at is not None:
                os.ftruncate(fd, decoded.truncate_at)
                os.fsync(fd)
                size = decoded.truncate_at
            records: dict[str, CaptureLifecycleRecord] = {}
            compaction_epoch = 1
            for frame in decoded.frames:
                if frame.compaction_epoch < compaction_epoch:
                    raise CaptureLedgerError("lifecycle compaction epoch regressed")
                compaction_epoch = frame.compaction_epoch
                raw_capture_id = frame.record.get("capture_id")
                previous = records.get(raw_capture_id) if isinstance(raw_capture_id, str) else None
                if frame.format_version == 1:
                    record = _legacy_record_from_dict(
                        frame.record,
                        revision=1 if previous is None else previous.revision + 1,
                        compaction_epoch=frame.compaction_epoch,
                    )
                else:
                    record = replace(
                        _record_from_dict(frame.record),
                        compaction_epoch=frame.compaction_epoch,
                    )
                if previous is not None:
                    if record.revision != previous.revision + 1:
                        raise CaptureLedgerError("lifecycle record revision gap")
                    if (
                        previous.manifest_bytes
                        and record.manifest_bytes != previous.manifest_bytes
                    ):
                        raise CaptureLedgerError("immutable FINAL manifest changed")
                records[record.capture_id] = record
            return records, compaction_epoch, size
        finally:
            os.close(fd)

    def _append_locked(
        self,
        record: CaptureLifecycleRecord,
        records: Mapping[str, CaptureLifecycleRecord],
        compaction_epoch: int,
        size: int,
    ) -> None:
        _validate_record(record)
        previous = records.get(record.capture_id)
        if previous is None:
            if record.revision != 1:
                raise CaptureLedgerError("new lifecycle record must start at revision one")
        elif record.revision != previous.revision + 1:
            raise CaptureLedgerError("stale lifecycle record revision")
        if (
            previous is not None
            and previous.manifest_bytes
            and record.manifest_bytes != previous.manifest_bytes
        ):
            raise CaptureLedgerError("immutable FINAL manifest changed")
        try:
            frame = _capture_ledger.encode_frame(
                _record_to_dict(record),
                compaction_epoch=compaction_epoch,
            )
        except _capture_ledger.LedgerCodecError as exc:
            raise CaptureLedgerError(str(exc)) from exc
        if size + len(frame) > _COMPACTION_THRESHOLD_BYTES:
            latest = dict(records)
            latest[record.capture_id] = record
            self._compact_locked(latest, compaction_epoch + 1)
            return
        fd = self._open_ledger()
        try:
            _capture_ledger.write_all(fd, frame)
            os.fsync(fd)
        except _capture_ledger.LedgerCodecError as exc:
            raise CaptureLedgerError(str(exc)) from exc
        finally:
            os.close(fd)

    def _compact_locked(
        self,
        records: Mapping[str, CaptureLifecycleRecord],
        compaction_epoch: int,
    ) -> None:
        active = [
            record for record in records.values() if record.state is not CaptureState.DELETED
        ]
        tombstones = sorted(
            (record for record in records.values() if record.state is CaptureState.DELETED),
            key=lambda record: (record.next_attempt_at, record.capture_id),
            reverse=True,
        )[:_MAX_TOMBSTONES]
        compacted = sorted(active + tombstones, key=lambda record: record.capture_id)
        try:
            frames = [
                _capture_ledger.encode_frame(
                    _record_to_dict(record),
                    compaction_epoch=compaction_epoch,
                )
                for record in compacted
            ]
        except _capture_ledger.LedgerCodecError as exc:
            raise CaptureLedgerError(str(exc)) from exc
        if sum(map(len, frames)) > _MAX_COMPACTION_BYTES:
            raise CaptureLedgerError("lifecycle compaction exceeds bound")
        temp_name = f".capture-lifecycle-compact-{secrets.token_hex(8)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW
        fd = os.open(temp_name, flags, 0o600, dir_fd=self._root_fd)
        try:
            _validate_control_file(fd, temp_name)
            for frame in frames:
                _capture_ledger.write_all(fd, frame)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            try:
                os.unlink(temp_name, dir_fd=self._root_fd)
            except OSError:
                pass
            raise
        else:
            os.close(fd)
        try:
            os.replace(
                temp_name,
                LEDGER_NAME,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
            )
        except BaseException:
            try:
                os.unlink(temp_name, dir_fd=self._root_fd)
            except OSError:
                pass
            raise
        os.fsync(self._root_fd)

    @staticmethod
    def _authority_for(record: CaptureLifecycleRecord) -> CaptureWriteAuthority:
        return _capture_snapshot._make_write_authority(
            record.capture_id,
            record.incarnation,
            record.revision,
        )

    def _transition_locked(
        self,
        *,
        records: dict[str, CaptureLifecycleRecord],
        compaction_epoch: int,
        ledger_size: int,
        authority: CaptureWriteAuthority,
        allowed_states: set[CaptureState],
        transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
    ) -> CaptureLifecycleRecord:
        if type(authority) is not CaptureWriteAuthority:
            raise CaptureLifecycleError("transition requires capture write authority")
        previous = records.get(authority.capture_id)
        if (
            previous is None
            or previous.incarnation != authority.incarnation
            or previous.revision != authority.expected_revision
            or previous.state not in allowed_states
        ):
            raise CaptureLifecycleError("stale or invalid lifecycle transition")
        candidate = transform(previous)
        if (
            type(candidate) is not CaptureLifecycleRecord
            or candidate.capture_id != previous.capture_id
            or candidate.incarnation != previous.incarnation
            or candidate.revision != previous.revision + 1
        ):
            raise CaptureLifecycleError("transition did not produce one valid successor")
        self._append_locked(
            candidate,
            records,
            compaction_epoch,
            ledger_size,
        )
        return candidate

    def _transition(
        self,
        authority: CaptureWriteAuthority,
        *,
        allowed_states: set[CaptureState],
        transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
    ) -> CaptureLifecycleRecord:
        with self._locked():
            records, compaction_epoch, size = self._load_locked()
            return self._transition_locked(
                records=records,
                compaction_epoch=compaction_epoch,
                ledger_size=size,
                authority=authority,
                allowed_states=allowed_states,
                transform=transform,
            )

    def reserve_capture(self, capture_id: str) -> CaptureLifecycleRecord:
        if not _CAPTURE_ID_RE.fullmatch(capture_id):
            raise CaptureLifecycleError("invalid capture id")
        now = self._wall_clock()
        nonce = secrets.token_hex(8)
        incarnation = secrets.token_hex(16)
        record = CaptureLifecycleRecord(
            capture_id=capture_id,
            state=CaptureState.RESERVED,
            staging_name=f".capture-staging-{capture_id}-{nonce}",
            public_name=f"shell_{capture_id}.log",
            project_identity=self._project_identity,
            root_identity=self._root_identity,
            created_at=now,
            next_attempt_at=now + _RETENTION_SECONDS,
            incarnation=incarnation,
            revision=1,
        )
        with self._locked():
            records, compaction_epoch, size = self._load_locked()
            previous = records.get(capture_id)
            if previous is not None and previous.state is not CaptureState.DELETED:
                raise CaptureLifecycleError("capture id already reserved")
            if (
                sum(current.state is not CaptureState.DELETED for current in records.values())
                >= MAX_ACTIVE_RECORDS
            ):
                raise CaptureLedgerError("active lifecycle record bound reached")
            self._append_locked(record, records, compaction_epoch, size)
        return record

    def mark_staged(
        self,
        authority: CaptureWriteAuthority,
        artifact_identity: tuple[int, int],
    ) -> CaptureLifecycleRecord:
        if (
            not isinstance(artifact_identity, tuple)
            or len(artifact_identity) != 2
            or any(
                not isinstance(part, int) or isinstance(part, bool) or part < 0
                for part in artifact_identity
            )
        ):
            raise CaptureLifecycleError("invalid staged artifact identity")
        return self._transition(
            authority,
            allowed_states={CaptureState.RESERVED},
            transform=lambda record: replace(
                record,
                state=CaptureState.STAGED,
                artifact_identity=artifact_identity,
                revision=record.revision + 1,
            ),
        )

    def mark_published(
        self,
        authority: CaptureWriteAuthority,
    ) -> CaptureLifecycleRecord:
        return self._transition(
            authority,
            allowed_states={CaptureState.STAGED},
            transform=lambda record: replace(
                record,
                state=CaptureState.PUBLISHED_WRITING,
                revision=record.revision + 1,
            ),
        )

    def create_artifact(
        self,
        capture_id: str,
    ) -> tuple[int, int, str, tuple[int, int], CaptureWriteAuthority]:
        """Create, lease, and durably publish one capture artifact."""

        record = self.reserve_capture(capture_id)
        authority = self._authority_for(record)
        fd = -1
        lease_fd = -1
        try:
            fd = os.open(record.staging_name, _ARTIFACT_FLAGS, 0o600, dir_fd=self._root_fd)
            value = os.fstat(fd)
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_nlink != 1
                or value.st_uid != os.geteuid()
                or value.st_mode & _UNTRUSTED_WRITE_BITS
            ):
                raise CaptureLifecycleError("unsafe staged capture artifact")
            identity = _identity(value)
            lease_fd = self.acquire_writer_lease(fd)
            staged = self.mark_staged(authority, identity)
            authority = self._authority_for(staged)
            os.fsync(fd)
            os.link(
                record.staging_name,
                record.public_name,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            staging_value = os.stat(
                record.staging_name,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            public_value = os.stat(
                record.public_name,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            if (
                _identity(staging_value) != identity
                or _identity(public_value) != identity
                or staging_value.st_nlink != 2
                or public_value.st_nlink != 2
            ):
                raise CaptureLifecycleError("capture publication identity changed")
            os.unlink(record.staging_name, dir_fd=self._root_fd)
            os.fsync(self._root_fd)
            public_value = os.stat(
                record.public_name,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            if _identity(public_value) != identity or public_value.st_nlink != 1:
                raise CaptureLifecycleError("capture publication did not settle")
            published = self.mark_published(authority)
            authority = self._authority_for(published)
            return fd, lease_fd, record.public_name, identity, authority
        except (CaptureLifecycleError, OSError) as primary_error:
            try:
                current = self.get_record(capture_id)
                if current is not None and current.state in {
                    CaptureState.RESERVED,
                    CaptureState.STAGED,
                    CaptureState.PUBLISHED_WRITING,
                }:
                    self.commit_capture_failure(
                        self._authority_for(current),
                        CaptureFailureEvidence(
                            stage="artifact_publication",
                            detail=f"{type(primary_error).__name__}: {primary_error}",
                        ),
                        observed_size=os.fstat(fd).st_size if fd >= 0 else 0,
                    )
            except (CaptureAuthorityError, CaptureLifecycleError, OSError) as recovery_error:
                primary_error.add_note(
                    "failed-state recovery also failed: "
                    f"{type(recovery_error).__name__}: {recovery_error}"
                )
            if lease_fd >= 0:
                os.close(lease_fd)
            if fd >= 0:
                os.close(fd)
            raise

    def commit_capture_failure(
        self,
        authority: CaptureWriteAuthority,
        evidence: CaptureFailureEvidence,
        *,
        observed_size: int,
    ) -> CaptureLifecycleRecord:
        if type(evidence) is not CaptureFailureEvidence:
            raise CaptureLifecycleError("failure transition requires typed evidence")
        if not _plain_int(observed_size):
            raise CaptureLifecycleError("invalid observed capture size")
        now = self._wall_clock()
        return self._transition(
            authority,
            allowed_states={
                CaptureState.RESERVED,
                CaptureState.STAGED,
                CaptureState.PUBLISHED_WRITING,
            },
            transform=lambda record: replace(
                record,
                state=CaptureState.FAILED,
                retention_at=now,
                next_attempt_at=now + _RETENTION_SECONDS,
                observed_size=observed_size,
                failure=evidence,
                retention_phase=CaptureRetentionPhase.ACTIVE,
                revision=record.revision + 1,
            ),
        )

    def commit_verified_snapshot(
        self,
        verified: VerifiedCaptureSnapshot,
        *,
        issue_reference: bool,
    ) -> FinalizedCapture:
        if type(verified) is not VerifiedCaptureSnapshot or not isinstance(issue_reference, bool):
            raise CaptureLifecycleError("invalid verified finalization request")
        base = verified.manifest
        with self._locked():
            records, compaction_epoch, size = self._load_locked()
            previous = records.get(base.capture_id)
            if (
                previous is None
                or previous.state is not CaptureState.PUBLISHED_WRITING
                or previous.incarnation != base.incarnation
                or previous.revision + 1 != base.finalized_at_revision
                or previous.project_identity != base.project_identity
                or previous.root_identity != base.root_identity
                or previous.public_name != base.carrier_name
                or previous.artifact_identity != base.carrier_identity
            ):
                raise CaptureLifecycleError("verified snapshot does not match write authority")
            token: str | None = None
            reference_hash: str | None = None
            reference_expiry: float | None = None
            if issue_reference:
                reference_expiry = min(
                    base.finalized_at + _REFERENCE_LIFETIME_SECONDS,
                    base.retention_deadline,
                )
                token, reference_hash = _capture_snapshot._issue_capture_reference(
                    verified,
                    expiry=reference_expiry,
                )
            finalized = _capture_snapshot._bind_finalized_snapshot(
                verified,
                reference_token=token,
                reference_hash=reference_hash,
                reference_expiry=reference_expiry,
            )
            manifest = finalized.snapshot.manifest
            manifest_bytes = _capture_snapshot.encode_capture_final_manifest(manifest)
            candidate = replace(
                previous,
                state=CaptureState.FINALIZED,
                revision=previous.revision + 1,
                finalized_at_revision=manifest.finalized_at_revision,
                retention_at=manifest.finalized_at,
                next_attempt_at=manifest.retention_deadline,
                manifest=manifest,
                manifest_bytes=manifest_bytes,
                reference_status=(
                    CaptureReferenceStatus.ISSUED
                    if finalized.issuance is not None
                    else CaptureReferenceStatus.NOT_REQUESTED
                ),
                delivery_status=CaptureDeliveryStatus.NOT_ATTEMPTED,
                retention_phase=CaptureRetentionPhase.ACTIVE,
            )
            self._append_locked(candidate, records, compaction_epoch, size)
        return finalized

    def publish_reference(
        self,
        finalized: FinalizedCapture,
    ) -> PublishedCaptureReference:
        if type(finalized) is not FinalizedCapture or finalized.issuance is None:
            raise CaptureLifecycleError("publication requires an issued finalized capture")
        issuance = finalized.issuance
        authority = _capture_snapshot._make_write_authority(
            issuance.snapshot.manifest.capture_id,
            issuance.snapshot.manifest.incarnation,
            finalized.finalized_at_revision,
        )
        self._transition(
            authority,
            allowed_states={CaptureState.FINALIZED},
            transform=lambda record: replace(
                record,
                reference_status=CaptureReferenceStatus.PUBLISHED,
                revision=record.revision + 1,
            ),
        )
        return _capture_snapshot._make_published_reference(issuance)

    def mark_reference_unavailable(
        self,
        finalized: FinalizedCapture,
        *,
        reason_code: str,
    ) -> UnavailableCaptureReference:
        if type(finalized) is not FinalizedCapture:
            raise CaptureLifecycleError("unavailable transition requires finalized capture")
        snapshot = finalized.snapshot
        record = self.get_record(snapshot.manifest.capture_id)
        if record is None:
            raise CaptureLifecycleError("finalized capture record is unavailable")
        authority = self._authority_for(record)
        self._transition(
            authority,
            allowed_states={CaptureState.FINALIZED},
            transform=lambda current: replace(
                current,
                reference_status=CaptureReferenceStatus.UNAVAILABLE,
                revision=current.revision + 1,
            ),
        )
        return _capture_snapshot._make_unavailable_reference(snapshot, reason_code)

    def get_record(self, capture_id: str) -> CaptureLifecycleRecord | None:
        with self._locked():
            records, _compaction_epoch, _size = self._load_locked()
            return records.get(capture_id)

    @staticmethod
    def acquire_writer_lease(artifact_fd: int) -> int:
        try:
            lease_fd = os.dup(artifact_fd)
        except OSError as exc:
            raise CaptureLifecycleError("cannot duplicate writer lease descriptor") from exc
        try:
            fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lease_fd
        except OSError as exc:
            os.close(lease_fd)
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise CaptureLifecycleError("writer lease unexpectedly contended") from exc
            raise CaptureLifecycleError("writer lease capability unavailable") from exc

    def _observe(
        self,
        name: str,
        expected: tuple[int, int] | None,
        *,
        valid_name: re.Pattern[str],
    ) -> _ObservedArtifact | None:
        return _capture_sweep.observe_artifact(
            root_fd=self._root_fd,
            name=name,
            expected=expected,
            valid_name=valid_name,
            open_flags=_OBSERVE_FLAGS,
            untrusted_write_bits=_UNTRUSTED_WRITE_BITS,
            lifecycle_error=CaptureLifecycleError,
        )

    @staticmethod
    def _try_artifact_lease(observed: _ObservedArtifact) -> None:
        try:
            fcntl.flock(observed.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise _WriterLive from exc
            raise CaptureLifecycleError("artifact lease capability failure") from exc

    def _normalize_abandoned(
        self,
        record: CaptureLifecycleRecord,
    ) -> tuple[CaptureLifecycleRecord, _ObservedArtifact | None]:
        return _capture_sweep.normalize_abandoned(
            record,
            root_fd=self._root_fd,
            observe=self._observe,
            try_lease=self._try_artifact_lease,
            staging_name_pattern=_STAGING_NAME_RE,
            public_name_pattern=_PUBLIC_NAME_RE,
            wall_clock=self._wall_clock,
        )

    def _deleting_record(
        self,
        record: CaptureLifecycleRecord,
    ) -> CaptureLifecycleRecord:
        return _capture_sweep.deleting_record(
            record,
            nonce=secrets.token_hex(8),
        )

    def _quarantine_delete(self, record: CaptureLifecycleRecord) -> int:
        return _capture_sweep.quarantine_delete(
            record,
            root_fd=self._root_fd,
            observe=self._observe,
            try_lease=self._try_artifact_lease,
            public_name_pattern=_PUBLIC_NAME_RE,
            quarantine_name_pattern=_QUARANTINE_NAME_RE,
        )

    def _retry(self, record: CaptureLifecycleRecord) -> CaptureLifecycleRecord:
        return _capture_sweep.retry_record(
            record,
            now=self._wall_clock(),
            max_retry_seconds=_MAX_RETRY_SECONDS,
        )

    def _sweep_one(self, capture_id: str) -> tuple[str, int, int]:
        with self._locked(blocking=False):
            records, compaction_epoch, size = self._load_locked()
            record = records.get(capture_id)
            now = self._wall_clock()
            if (
                record is None
                or record.state in {CaptureState.DELETED, CaptureState.TAMPERED}
                or record.next_attempt_at > now
            ):
                return ("not_due", 0, 0)
            try:
                if (
                    record.project_identity != self._project_identity
                    or record.root_identity != self._root_identity
                ):
                    raise _Tampered
                if record.state in {
                    CaptureState.RESERVED,
                    CaptureState.STAGED,
                    CaptureState.PUBLISHED_WRITING,
                }:
                    record, lease = self._normalize_abandoned(record)
                    if lease is not None:
                        os.close(lease.fd)
                    if record.state is CaptureState.DELETED:
                        self._append_locked(record, records, compaction_epoch, size)
                        return ("deleted", 0, 0)
                    self._append_locked(record, records, compaction_epoch, size)
                    records, compaction_epoch, size = self._load_locked()
                    record = records[capture_id]

                if (
                    record.reference_status
                    in {
                        CaptureReferenceStatus.ISSUED,
                        CaptureReferenceStatus.PUBLISHED,
                    }
                    and record.manifest is not None
                    and record.manifest.reference_expiry is not None
                    and now >= record.manifest.reference_expiry
                ):
                    expired = replace(
                        record,
                        reference_status=CaptureReferenceStatus.EXPIRED,
                        revision=record.revision + 1,
                    )
                    self._append_locked(
                        expired,
                        records,
                        compaction_epoch,
                        size,
                    )
                    records, compaction_epoch, size = self._load_locked()
                    record = records[capture_id]
                deleting = self._deleting_record(record)
                if deleting is not record:
                    self._append_locked(
                        deleting,
                        records,
                        compaction_epoch,
                        size,
                    )
                    records, compaction_epoch, size = self._load_locked()
                    record = records[capture_id]
                deleted_bytes = self._quarantine_delete(record)
                deleted = replace(
                    record,
                    state=CaptureState.DELETED,
                    next_attempt_at=now,
                    retention_phase=CaptureRetentionPhase.DELETED,
                    revision=record.revision + 1,
                )
                self._append_locked(
                    deleted,
                    records,
                    compaction_epoch,
                    size,
                )
                return ("deleted", deleted_bytes, 0)
            except _WriterLive:
                live = replace(
                    record,
                    next_attempt_at=now + 30.0,
                    revision=record.revision + 1,
                )
                self._append_locked(
                    live,
                    records,
                    compaction_epoch,
                    size,
                )
                return ("writer_live", 0, 0)
            except _Tampered:
                tampered = replace(
                    record,
                    state=CaptureState.TAMPERED,
                    retention_phase=CaptureRetentionPhase.TAMPERED,
                    revision=record.revision + 1,
                )
                self._append_locked(
                    tampered,
                    records,
                    compaction_epoch,
                    size,
                )
                return ("tampered", 0, 0)
            except (CaptureLifecycleError, OSError):
                retry = self._retry(record)
                self._append_locked(
                    retry,
                    records,
                    compaction_epoch,
                    size,
                )
                return ("error", 0, 1)

    def _due_ids(self, now: float) -> list[str]:
        with self._locked(blocking=False):
            records, _generation, _size = self._load_locked()
        return _capture_sweep.due_capture_ids(
            records.values(),
            now,
            {CaptureState.DELETED, CaptureState.TAMPERED},
        )

    def sweep(
        self,
        *,
        max_items: int = 32,
        max_duration_seconds: float = 0.05,
    ) -> CaptureCleanupOutcome:
        if max_items <= 0 or max_duration_seconds <= 0:
            raise CaptureLifecycleError("cleanup bounds must be positive")
        return _capture_sweep.run_bounded_sweep(
            max_items=max_items,
            max_duration_seconds=max_duration_seconds,
            monotonic=self._monotonic,
            wall_clock=self._wall_clock,
            due_ids=self._due_ids,
            sweep_one=self._sweep_one,
        )
