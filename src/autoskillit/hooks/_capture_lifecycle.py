from __future__ import annotations

import errno
import fcntl
import importlib
import os
import re
import secrets
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture_lifecycle", "autoskillit.hooks._capture_lifecycle"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture lifecycle module identity")

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _delivery as _capture_delivery
    from autoskillit.hooks._capture import _ledger as _capture_ledger
    from autoskillit.hooks._capture import _reader as _capture_reader
    from autoskillit.hooks._capture import _resolver as _capture_resolver
    from autoskillit.hooks._capture import _snapshot as _capture_snapshot
    from autoskillit.hooks._capture import _sweep as _capture_sweep
    from autoskillit.hooks._capture import _syntax as _capture_syntax
    from autoskillit.hooks._capture import _types as _capture_types
else:
    _CAPTURE_PACKAGE = f"{__package__}._capture" if __package__ else "_capture"
    _capture_delivery = importlib.import_module(f"{_CAPTURE_PACKAGE}._delivery")
    _capture_ledger = importlib.import_module(f"{_CAPTURE_PACKAGE}._ledger")
    _capture_reader = importlib.import_module(f"{_CAPTURE_PACKAGE}._reader")
    _capture_resolver = importlib.import_module(f"{_CAPTURE_PACKAGE}._resolver")
    _capture_snapshot = importlib.import_module(f"{_CAPTURE_PACKAGE}._snapshot")
    _capture_sweep = importlib.import_module(f"{_CAPTURE_PACKAGE}._sweep")
    _capture_syntax = importlib.import_module(f"{_CAPTURE_PACKAGE}._syntax")
    _capture_types = importlib.import_module(f"{_CAPTURE_PACKAGE}._types")

CaptureCleanupOutcome = _capture_types.CaptureCleanupOutcome
_ObservedArtifact = _capture_types.ObservedArtifact
_LockContended = _capture_types.LockContended
_CarrierLeaseLive = _capture_types.CarrierLeaseLive

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
    "CaptureSnapshotStatus",
    "CaptureStatus",
    "CaptureState",
    "CaptureTransitionCommittedError",
]

FRAME_MAGIC = _capture_ledger.FRAME_MAGIC
LEDGER_NAME = ".capture-lifecycle.ledger"
LOCK_NAME = ".capture-lifecycle.lock"
MAX_LEDGER_BYTES = _capture_ledger.MAX_LEDGER_BYTES
MAX_ACTIVE_RECORDS = 4096

_RETENTION_SECONDS = 3600.0
_REFERENCE_LIFETIME_SECONDS = 1800.0
_MAX_RETRY_SECONDS = 3600.0
_COMPACTION_THRESHOLD_BYTES = 3 * 1024 * 1024
_MAX_COMPACTION_BYTES = 4 * 1024 * 1024
_MAX_TOMBSTONES = 256
_CAPTURE_ID_RE = _capture_syntax.CAPTURE_ID_RE
_PUBLIC_NAME_RE = re.compile(r"^shell_[0-9a-f]{16}\.log$")
_STAGING_NAME_RE = re.compile(r"^\.capture-staging-[0-9a-f]{16}-[0-9a-f]{16}$")
_QUARANTINE_NAME_RE = re.compile(r"^\.capture-quarantine-[0-9a-f]{16}-[0-9a-f]{16}$")
_CLOEXEC, _NOFOLLOW, _NONBLOCK = os.O_CLOEXEC, os.O_NOFOLLOW, os.O_NONBLOCK
_CONTROL_FLAGS = os.O_RDWR | os.O_CREAT | _CLOEXEC | _NOFOLLOW
_OBSERVE_FLAGS = os.O_RDWR | _CLOEXEC | _NOFOLLOW | _NONBLOCK
_ARTIFACT_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW
_UNTRUSTED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
_STORE_FACTORY_TOKEN = object()


class CaptureLifecycleError(RuntimeError):
    pass


class CaptureLedgerError(CaptureLifecycleError):
    pass


CaptureState = _capture_ledger.CaptureState
CaptureReferenceStatus = _capture_ledger.CaptureReferenceStatus
CaptureDeliveryStatus = _capture_ledger.CaptureDeliveryStatus
CaptureRetentionPhase = _capture_ledger.CaptureRetentionPhase
CaptureSnapshotStatus = _capture_ledger.CaptureSnapshotStatus
CaptureStatus = _capture_ledger.CaptureStatus
CaptureLifecycleRecord = _capture_ledger.CaptureLifecycleRecord
CaptureTransitionCommittedError = _capture_ledger.CaptureTransitionCommittedError


def _identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


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


def _validate_successor(
    previous: CaptureLifecycleRecord,
    candidate: CaptureLifecycleRecord,
) -> None:
    try:
        _capture_ledger.validate_successor(previous, candidate)
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
    def __init__(
        self,
        root_fd: int,
        *,
        project_identity: tuple[int, int],
        root_identity: tuple[int, int],
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _STORE_FACTORY_TOKEN:
            raise CaptureLifecycleError("CaptureLifecycleStore must be factory-created")
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
        store = cls(
            getattr(root, "fd"),
            project_identity=(anchor_identity.device, anchor_identity.inode),
            root_identity=(root_identity.device, root_identity.inode),
            wall_clock=wall_clock,
            monotonic=monotonic,
            _factory_token=_STORE_FACTORY_TOKEN,
        )
        store._normalize_interrupted_deliveries()
        return store

    def _normalize_interrupted_deliveries(self) -> None:
        _capture_delivery.normalize_interrupted_deliveries(
            self,
            lifecycle_error=CaptureLifecycleError,
            lease_live=_CarrierLeaseLive,
            tampered=_capture_types.Tampered,
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
            source_versions: dict[str, int] = {}
            compaction_epoch = 1
            saw_legacy = False
            for frame in decoded.frames:
                if frame.compaction_epoch < compaction_epoch:
                    raise CaptureLedgerError("lifecycle compaction epoch regressed")
                compaction_epoch = frame.compaction_epoch
                raw_capture_id = frame.record.get("capture_id")
                previous = records.get(raw_capture_id) if isinstance(raw_capture_id, str) else None
                if frame.format_version == 1:
                    if previous is not None and source_versions[previous.capture_id] != 1:
                        raise CaptureLedgerError("legacy frame follows current lifecycle state")
                    saw_legacy = True
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
                if previous is not None and frame.format_version != 1:
                    _validate_successor(previous, record)
                records[record.capture_id] = record
                source_versions[record.capture_id] = frame.format_version
            if saw_legacy:
                self._compact_locked(records, compaction_epoch + 1)
                return self._load_locked()
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
        previous = records.get(record.capture_id)
        if previous is None:
            if record.revision != 1:
                raise CaptureLedgerError("new lifecycle record must start at revision one")
        else:
            _validate_successor(previous, record)
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
        except BaseException as primary_error:
            try:
                os.close(fd)
            except OSError as cleanup_error:
                primary_error.add_note(
                    "ledger descriptor cleanup also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            if isinstance(primary_error, _capture_ledger.LedgerCodecError):
                raise CaptureLedgerError(str(primary_error)) from primary_error
            raise
        try:
            os.close(fd)
        except OSError as exc:
            raise CaptureTransitionCommittedError(
                "lifecycle transition committed before descriptor cleanup failed"
            ) from exc

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

    def _transition_current(
        self,
        capture_id: str,
        incarnation: str,
        *,
        allowed_states: set[CaptureState],
        transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
    ) -> CaptureLifecycleRecord:
        with self._locked():
            records, compaction_epoch, size = self._load_locked()
            previous = records.get(capture_id)
            if previous is None or previous.incarnation != incarnation:
                raise CaptureLifecycleError("capture transition authority is unavailable")
            return self._transition_locked(
                records=records,
                compaction_epoch=compaction_epoch,
                ledger_size=size,
                authority=self._authority_for(previous),
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
        if not _capture_ledger._plain_int(observed_size):
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
                capture_status=CaptureStatus.FAILED,
                snapshot_status=CaptureSnapshotStatus.ABSENT,
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
        candidate: CaptureLifecycleRecord | None = None
        finalized: FinalizedCapture | None = None
        try:
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
                candidate = replace(
                    previous,
                    state=CaptureState.FINALIZED,
                    revision=previous.revision + 1,
                    finalized_at_revision=manifest.finalized_at_revision,
                    retention_at=manifest.finalized_at,
                    next_attempt_at=manifest.retention_deadline,
                    manifest=manifest,
                    manifest_bytes=_capture_snapshot.encode_capture_final_manifest(manifest),
                    capture_status=CaptureStatus.COMPLETE,
                    snapshot_status=CaptureSnapshotStatus.VERIFIED,
                    reference_status=(
                        CaptureReferenceStatus.ISSUED
                        if finalized.issuance is not None
                        else CaptureReferenceStatus.NOT_REQUESTED
                    ),
                    delivery_status=CaptureDeliveryStatus.NOT_ATTEMPTED,
                    retention_phase=CaptureRetentionPhase.ACTIVE,
                )
                self._transition_locked(
                    records=records,
                    compaction_epoch=compaction_epoch,
                    ledger_size=size,
                    authority=self._authority_for(previous),
                    allowed_states={CaptureState.PUBLISHED_WRITING},
                    transform=lambda _current: candidate,
                )
        except CaptureTransitionCommittedError as commit_error:
            if candidate is None or finalized is None:
                raise
            try:
                current = self.get_record(base.capture_id)
            except (RuntimeError, OSError) as recovery_error:
                commit_error.add_note(
                    "FINAL reconciliation failed: "
                    f"{type(recovery_error).__name__}: {recovery_error}"
                )
            else:
                if (
                    current is not None
                    and replace(
                        current,
                        compaction_epoch=candidate.compaction_epoch,
                    )
                    == candidate
                ):
                    return finalized
            raise
        if finalized is None:
            raise CaptureLifecycleError("verified finalization produced no authority")
        return finalized

    def publish_reference(self, finalized: FinalizedCapture) -> PublishedCaptureReference:
        return _capture_delivery.publish_reference(
            self,
            finalized,
            lifecycle_error=CaptureLifecycleError,
        )

    def mark_reference_unavailable(
        self,
        finalized: FinalizedCapture,
        *,
        reason_code: str,
    ) -> UnavailableCaptureReference:
        return _capture_delivery.mark_reference_unavailable(
            self,
            finalized,
            reason_code=reason_code,
            lifecycle_error=CaptureLifecycleError,
        )

    def revoke_reference(self, finalized: FinalizedCapture) -> CaptureLifecycleRecord:
        return _capture_delivery.revoke_reference(
            self,
            finalized,
            lifecycle_error=CaptureLifecycleError,
        )

    def transition_delivery(
        self,
        value: _capture_delivery.DeliveryValue,
        *,
        expected: CaptureDeliveryStatus,
        target: CaptureDeliveryStatus,
    ) -> CaptureLifecycleRecord:
        return _capture_delivery.transition_delivery(
            self,
            value,
            expected=expected,
            target=target,
            lifecycle_error=CaptureLifecycleError,
        )

    def mark_delivery_unknown(
        self, value: _capture_delivery.DeliveryValue
    ) -> CaptureLifecycleRecord:
        return _capture_delivery.mark_delivery_unknown(
            self,
            value,
            lifecycle_error=CaptureLifecycleError,
        )

    def recover_interrupted_delivery(self, capture_id: str) -> CaptureLifecycleRecord:
        return _capture_delivery.recover_interrupted_delivery(
            self, capture_id, lifecycle_error=CaptureLifecycleError
        )

    def get_record(self, capture_id: str) -> CaptureLifecycleRecord | None:
        with self._locked():
            records, _compaction_epoch, _size = self._load_locked()
            return records.get(capture_id)

    def open_verified_capture(self, token: str) -> _capture_reader.VerifiedCaptureReader:
        return _capture_resolver.open_verified_capture(
            self,
            token,
            lifecycle_error=CaptureLifecycleError,
        )

    def _adopt_verified_capture(
        self, finalized: FinalizedCapture, fd: int
    ) -> _capture_reader.VerifiedCaptureReader:
        return _capture_resolver.adopt_verified_capture(
            self,
            finalized,
            fd,
            lifecycle_error=CaptureLifecycleError,
        )

    @staticmethod
    def acquire_writer_lease(artifact_fd: int) -> int:
        return _capture_resolver.acquire_writer_lease(
            artifact_fd,
            lifecycle_error=CaptureLifecycleError,
        )

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
                raise _CarrierLeaseLive from exc
            raise CaptureLifecycleError("artifact lease capability failure") from exc

    def _normalize_abandoned(
        self,
        record: CaptureLifecycleRecord,
        *,
        preleased: _ObservedArtifact | None = None,
        lease_checked: bool = False,
    ) -> tuple[CaptureLifecycleRecord, _ObservedArtifact | None]:
        return _capture_sweep.normalize_abandoned(
            record,
            root_fd=self._root_fd,
            observe=self._observe,
            try_lease=self._try_artifact_lease,
            staging_name_pattern=_STAGING_NAME_RE,
            public_name_pattern=_PUBLIC_NAME_RE,
            wall_clock=self._wall_clock,
            preleased=preleased,
            lease_checked=lease_checked,
        )

    def _acquire_cleanup_lease(
        self,
        record: CaptureLifecycleRecord,
    ) -> _ObservedArtifact | None:
        return _capture_sweep.acquire_cleanup_lease(
            record,
            observe=self._observe,
            try_lease=self._try_artifact_lease,
            staging_name_pattern=_STAGING_NAME_RE,
            public_name_pattern=_PUBLIC_NAME_RE,
            quarantine_name_pattern=_QUARANTINE_NAME_RE,
        )

    def _deleting_record(
        self,
        record: CaptureLifecycleRecord,
    ) -> CaptureLifecycleRecord:
        return _capture_sweep.deleting_record(
            record,
            nonce=secrets.token_hex(8),
        )

    def _quarantine_delete(
        self,
        record: CaptureLifecycleRecord,
        authorize_delete: Callable[[], None] | None = None,
        *,
        preleased: _ObservedArtifact | None = None,
        lease_checked: bool = False,
    ) -> int:
        return _capture_sweep.quarantine_delete(
            record,
            root_fd=self._root_fd,
            observe=self._observe,
            try_lease=self._try_artifact_lease,
            authorize_delete=authorize_delete,
            public_name_pattern=_PUBLIC_NAME_RE,
            quarantine_name_pattern=_QUARANTINE_NAME_RE,
            preleased=preleased,
            lease_checked=lease_checked,
        )

    def _sweep_one(self, capture_id: str) -> tuple[str, int, int]:
        return _capture_sweep.sweep_one(
            self,
            capture_id,
            lifecycle_error=CaptureLifecycleError,
            max_retry_seconds=_MAX_RETRY_SECONDS,
        )

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
