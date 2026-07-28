"""Descriptor-relative durable lifecycle for retained shell captures.

The module is a stdlib-only dependency leaf.  Callers provide an already-open
capture-root descriptor; pathname authority never enters this layer.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import importlib
import json
import math
import os
import re
import secrets
import stat
import struct
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _types as _capture_types
else:
    _capture_types = importlib.import_module(
        f"{__package__}._capture._types" if __package__ else "_capture._types"
    )

CaptureCleanupOutcome = _capture_types.CaptureCleanupOutcome
_ObservedArtifact = _capture_types.ObservedArtifact
_Tampered = _capture_types.Tampered
_WriterLive = _capture_types.WriterLive

__all__ = [
    "CaptureCleanupOutcome",
    "CaptureLedgerError",
    "CaptureLifecycleError",
    "CaptureLifecycleRecord",
    "CaptureLifecycleStore",
    "CaptureState",
]

FRAME_MAGIC = b"ASCL"
LEDGER_NAME = ".capture-lifecycle.ledger"
LOCK_NAME = ".capture-lifecycle.lock"
MAX_FRAME_BYTES = 64 * 1024
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_ACTIVE_RECORDS = 4096

_FORMAT_VERSION = 1
_RETENTION_SECONDS = 3600.0
_MAX_RETRY_SECONDS = 3600.0
_COMPACTION_THRESHOLD_BYTES = 3 * 1024 * 1024
_MAX_COMPACTION_BYTES = 4 * 1024 * 1024
_MAX_TOMBSTONES = 256
_FRAME_HEADER_FORMAT = ">4sI"
_FRAME_HEADER_BYTES = 8
_CHECKSUM_BYTES = hashlib.sha256().digest_size
_CAPTURE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_NAME_RE = re.compile(r"^shell_[0-9a-f]{16}\.log$")
_STAGING_NAME_RE = re.compile(r"^\.capture-staging-[0-9a-f]{16}-[0-9a-f]{16}$")
_QUARANTINE_NAME_RE = re.compile(r"^\.capture-quarantine-[0-9a-f]{16}-[0-9a-f]{16}$")
_CLOEXEC = os.O_CLOEXEC
_NOFOLLOW = os.O_NOFOLLOW
_NONBLOCK = os.O_NONBLOCK
_CONTROL_FLAGS = os.O_RDWR | os.O_CREAT | _CLOEXEC | _NOFOLLOW
_OBSERVE_FLAGS = os.O_RDWR | _CLOEXEC | _NOFOLLOW | _NONBLOCK
_ARTIFACT_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW
_UNTRUSTED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


class CaptureLifecycleError(RuntimeError):
    """Raised when lifecycle authority or state cannot be established safely."""


class CaptureLedgerError(CaptureLifecycleError):
    """Raised when durable lifecycle state is corrupt or exceeds a bound."""


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


@dataclass(frozen=True, slots=True)
class CaptureLifecycleRecord:
    capture_id: str
    state: CaptureState
    staging_name: str
    public_name: str
    project_identity: tuple[int, int]
    root_identity: tuple[int, int]
    created_at: float
    next_attempt_at: float
    artifact_identity: tuple[int, int] | None = None
    retention_at: float | None = None
    size: int = 0
    sha256: str = ""
    retry_count: int = 0
    deletion_nonce: str = ""
    quarantine_name: str = ""


def _identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _record_to_dict(record: CaptureLifecycleRecord) -> dict[str, object]:
    return {
        "capture_id": record.capture_id,
        "state": record.state.value,
        "staging_name": record.staging_name,
        "public_name": record.public_name,
        "project_identity": list(record.project_identity),
        "root_identity": list(record.root_identity),
        "created_at": record.created_at,
        "next_attempt_at": record.next_attempt_at,
        "artifact_identity": (
            list(record.artifact_identity) if record.artifact_identity is not None else None
        ),
        "retention_at": record.retention_at,
        "size": record.size,
        "sha256": record.sha256,
        "retry_count": record.retry_count,
        "deletion_nonce": record.deletion_nonce,
        "quarantine_name": record.quarantine_name,
    }


def _pair(value: object, field: str) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(part, int) or isinstance(part, bool) for part in value)
    ):
        raise CaptureLedgerError(f"invalid {field}")
    return (value[0], value[1])


def _is_finite_timestamp(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _record_from_dict(value: object) -> CaptureLifecycleRecord:
    if not isinstance(value, dict):
        raise CaptureLedgerError("record is not an object")
    try:
        capture_id = value["capture_id"]
        state = CaptureState(value["state"])
        staging_name = value["staging_name"]
        public_name = value["public_name"]
        created_at = value["created_at"]
        next_attempt_at = value["next_attempt_at"]
        retention_at = value.get("retention_at")
        size = value.get("size", 0)
        sha256 = value.get("sha256", "")
        retry_count = value.get("retry_count", 0)
        deletion_nonce = value.get("deletion_nonce", "")
        quarantine_name = value.get("quarantine_name", "")
    except (KeyError, TypeError, ValueError) as exc:
        raise CaptureLedgerError("invalid lifecycle record") from exc
    if (
        not isinstance(capture_id, str)
        or not _CAPTURE_ID_RE.fullmatch(capture_id)
        or not isinstance(staging_name, str)
        or not _STAGING_NAME_RE.fullmatch(staging_name)
        or not isinstance(public_name, str)
        or not _PUBLIC_NAME_RE.fullmatch(public_name)
        or not _is_finite_timestamp(created_at)
        or not _is_finite_timestamp(next_attempt_at)
        or (retention_at is not None and not _is_finite_timestamp(retention_at))
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not isinstance(sha256, str)
        or bool(sha256)
        and not _SHA256_RE.fullmatch(sha256)
        or not isinstance(retry_count, int)
        or isinstance(retry_count, bool)
        or retry_count < 0
        or not isinstance(deletion_nonce, str)
        or not isinstance(quarantine_name, str)
        or (quarantine_name and not _QUARANTINE_NAME_RE.fullmatch(quarantine_name))
    ):
        raise CaptureLedgerError("invalid lifecycle record fields")
    artifact_value = value.get("artifact_identity")
    return CaptureLifecycleRecord(
        capture_id=capture_id,
        state=state,
        staging_name=staging_name,
        public_name=public_name,
        project_identity=_pair(value.get("project_identity"), "project identity"),
        root_identity=_pair(value.get("root_identity"), "root identity"),
        created_at=float(created_at),
        next_attempt_at=float(next_attempt_at),
        artifact_identity=(
            None if artifact_value is None else _pair(artifact_value, "artifact identity")
        ),
        retention_at=None if retention_at is None else float(retention_at),
        size=size,
        sha256=sha256,
        retry_count=retry_count,
        deletion_nonce=deletion_nonce,
        quarantine_name=quarantine_name,
    )


def _encode_frame(record: CaptureLifecycleRecord, generation: int) -> bytes:
    payload = json.dumps(
        {
            "format_version": _FORMAT_VERSION,
            "generation": generation,
            "record": _record_to_dict(record),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise CaptureLedgerError("lifecycle frame exceeds bound")
    return (
        struct.pack(_FRAME_HEADER_FORMAT, FRAME_MAGIC, len(payload))
        + payload
        + hashlib.sha256(payload).digest()
    )


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(fd, view[offset:])
        except InterruptedError:
            continue
        if written <= 0 or written > len(view) - offset:
            raise CaptureLedgerError("lifecycle ledger write made no progress")
        offset += written


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
    def _locked(self) -> Iterator[None]:
        self._validate_root()
        try:
            fd = os.open(LOCK_NAME, _CONTROL_FLAGS, 0o600, dir_fd=self._root_fd)
        except OSError as exc:
            raise CaptureLifecycleError("cannot open lifecycle lock") from exc
        try:
            _validate_control_file(fd, LOCK_NAME)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
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

            records: dict[str, CaptureLifecycleRecord] = {}
            generation = 1
            cursor = 0
            while cursor < len(data):
                remaining = len(data) - cursor
                if remaining < _FRAME_HEADER_BYTES:
                    os.ftruncate(fd, cursor)
                    os.fsync(fd)
                    size = cursor
                    break
                magic, declared = struct.unpack_from(_FRAME_HEADER_FORMAT, data, cursor)
                if magic != FRAME_MAGIC:
                    raise CaptureLedgerError("invalid lifecycle frame magic")
                if declared > MAX_FRAME_BYTES:
                    raise CaptureLedgerError("declared lifecycle frame exceeds bound")
                frame_end = cursor + _FRAME_HEADER_BYTES + declared + _CHECKSUM_BYTES
                if frame_end > len(data):
                    os.ftruncate(fd, cursor)
                    os.fsync(fd)
                    size = cursor
                    break
                payload_start = cursor + _FRAME_HEADER_BYTES
                payload = bytes(data[payload_start : payload_start + declared])
                checksum = bytes(data[payload_start + declared : frame_end])
                if hashlib.sha256(payload).digest() != checksum:
                    raise CaptureLedgerError("lifecycle frame checksum mismatch")
                try:
                    decoded = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CaptureLedgerError("invalid lifecycle frame payload") from exc
                if (
                    not isinstance(decoded, dict)
                    or decoded.get("format_version") != _FORMAT_VERSION
                    or not isinstance(decoded.get("generation"), int)
                    or isinstance(decoded.get("generation"), bool)
                    or decoded["generation"] < generation
                ):
                    raise CaptureLedgerError("invalid lifecycle frame metadata")
                generation = decoded["generation"]
                record = _record_from_dict(decoded.get("record"))
                records[record.capture_id] = record
                cursor = frame_end
            return records, generation, size
        finally:
            os.close(fd)

    def _append_locked(
        self,
        record: CaptureLifecycleRecord,
        records: Mapping[str, CaptureLifecycleRecord],
        generation: int,
        size: int,
    ) -> None:
        frame = _encode_frame(record, generation)
        if size + len(frame) > _COMPACTION_THRESHOLD_BYTES:
            latest = dict(records)
            latest[record.capture_id] = record
            self._compact_locked(latest, generation + 1)
            return
        fd = self._open_ledger()
        try:
            _write_all(fd, frame)
            os.fsync(fd)
        finally:
            os.close(fd)

    def _compact_locked(
        self,
        records: Mapping[str, CaptureLifecycleRecord],
        generation: int,
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
        frames = [_encode_frame(record, generation) for record in compacted]
        if sum(map(len, frames)) > _MAX_COMPACTION_BYTES:
            raise CaptureLedgerError("lifecycle compaction exceeds bound")
        temp_name = f".capture-lifecycle-compact-{secrets.token_hex(8)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW
        fd = os.open(temp_name, flags, 0o600, dir_fd=self._root_fd)
        try:
            _validate_control_file(fd, temp_name)
            for frame in frames:
                _write_all(fd, frame)
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

    def _commit(self, record: CaptureLifecycleRecord) -> CaptureLifecycleRecord:
        with self._locked():
            records, generation, size = self._load_locked()
            previous = records.get(record.capture_id)
            if (
                previous is None
                and sum(current.state is not CaptureState.DELETED for current in records.values())
                >= MAX_ACTIVE_RECORDS
            ):
                raise CaptureLedgerError("active lifecycle record bound reached")
            self._append_locked(record, records, generation, size)
        return record

    def reserve_capture(self, capture_id: str) -> CaptureLifecycleRecord:
        if not _CAPTURE_ID_RE.fullmatch(capture_id):
            raise CaptureLifecycleError("invalid capture id")
        now = self._wall_clock()
        nonce = secrets.token_hex(8)
        record = CaptureLifecycleRecord(
            capture_id=capture_id,
            state=CaptureState.RESERVED,
            staging_name=f".capture-staging-{capture_id}-{nonce}",
            public_name=f"shell_{capture_id}.log",
            project_identity=self._project_identity,
            root_identity=self._root_identity,
            created_at=now,
            next_attempt_at=now + _RETENTION_SECONDS,
        )
        with self._locked():
            records, generation, size = self._load_locked()
            previous = records.get(capture_id)
            if previous is not None and previous.state is not CaptureState.DELETED:
                raise CaptureLifecycleError("capture id already reserved")
            if (
                sum(current.state is not CaptureState.DELETED for current in records.values())
                >= MAX_ACTIVE_RECORDS
            ):
                raise CaptureLedgerError("active lifecycle record bound reached")
            self._append_locked(record, records, generation, size)
        return record

    def mark_staged(
        self,
        capture_id: str,
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
        record = self.get_record(capture_id)
        if record is None or record.state is not CaptureState.RESERVED:
            raise CaptureLifecycleError("invalid lifecycle transition to STAGED")
        return self._commit(
            replace(
                record,
                state=CaptureState.STAGED,
                artifact_identity=artifact_identity,
            )
        )

    def mark_published(self, capture_id: str) -> CaptureLifecycleRecord:
        record = self.get_record(capture_id)
        if record is None or record.state is not CaptureState.STAGED:
            raise CaptureLifecycleError("invalid lifecycle transition to PUBLISHED_WRITING")
        return self._commit(replace(record, state=CaptureState.PUBLISHED_WRITING))

    def create_artifact(self, capture_id: str) -> tuple[int, int, str, tuple[int, int]]:
        """Create, lease, and durably publish one capture artifact."""

        record = self.reserve_capture(capture_id)
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
            self.mark_staged(capture_id, identity)
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
            self.mark_published(capture_id)
            return fd, lease_fd, record.public_name, identity
        except (CaptureLifecycleError, OSError) as primary_error:
            try:
                self.finalize_capture(
                    capture_id,
                    size=os.fstat(fd).st_size if fd >= 0 else 0,
                    sha256="",
                    failed=True,
                )
            except (CaptureLifecycleError, OSError) as recovery_error:
                primary_error.add_note(
                    "failed-state recovery also failed: "
                    f"{type(recovery_error).__name__}: {recovery_error}"
                )
            if lease_fd >= 0:
                os.close(lease_fd)
            if fd >= 0:
                os.close(fd)
            raise

    def finalize_capture(
        self,
        capture_id: str,
        *,
        size: int,
        sha256: str,
        failed: bool,
    ) -> CaptureLifecycleRecord:
        if not isinstance(failed, bool):
            raise CaptureLifecycleError("invalid terminal capture failure annotation")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise CaptureLifecycleError("invalid terminal capture size")
        if (
            not isinstance(sha256, str)
            or bool(sha256)
            and not _SHA256_RE.fullmatch(sha256)
            or not failed
            and not sha256
        ):
            raise CaptureLifecycleError("invalid terminal capture digest")
        record = self.get_record(capture_id)
        if record is None or record.state not in {
            CaptureState.PUBLISHED_WRITING,
            CaptureState.STAGED,
            CaptureState.RESERVED,
        }:
            raise CaptureLifecycleError("invalid terminal lifecycle transition")
        if not failed and (
            record.state is not CaptureState.PUBLISHED_WRITING or record.artifact_identity is None
        ):
            raise CaptureLifecycleError("invalid successful capture finalization")
        now = self._wall_clock()
        return self._commit(
            replace(
                record,
                state=CaptureState.FAILED if failed else CaptureState.FINALIZED,
                retention_at=now,
                next_attempt_at=now + _RETENTION_SECONDS,
                size=size,
                sha256=sha256,
            )
        )

    def get_record(self, capture_id: str) -> CaptureLifecycleRecord | None:
        with self._locked():
            records, _generation, _size = self._load_locked()
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
        if not valid_name.fullmatch(name):
            raise _Tampered
        try:
            observed = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CaptureLifecycleError("cannot inspect managed capture") from exc
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or observed.st_mode & _UNTRUSTED_WRITE_BITS
            or observed.st_nlink not in (1, 2)
        ):
            raise _Tampered
        try:
            fd = os.open(name, _OBSERVE_FLAGS, dir_fd=self._root_fd)
        except OSError as exc:
            raise _Tampered from exc
        try:
            value = os.fstat(fd)
            identity = _identity(value)
            if (
                identity != _identity(observed)
                or (expected is not None and identity != expected)
                or not stat.S_ISREG(value.st_mode)
                or value.st_uid != os.geteuid()
                or value.st_mode & _UNTRUSTED_WRITE_BITS
                or value.st_nlink not in (1, 2)
            ):
                raise _Tampered
            return _ObservedArtifact(
                fd=fd,
                identity=identity,
                nlink=value.st_nlink,
                size=value.st_size,
            )
        except BaseException:
            os.close(fd)
            raise

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
        staging: _ObservedArtifact | None = None
        public: _ObservedArtifact | None = None
        lease_target: _ObservedArtifact | None = None
        lease_transferred = False
        try:
            staging = self._observe(
                record.staging_name,
                record.artifact_identity,
                valid_name=_STAGING_NAME_RE,
            )
            public = self._observe(
                record.public_name,
                record.artifact_identity,
                valid_name=_PUBLIC_NAME_RE,
            )
            lease_target = public or staging
            if lease_target is None:
                return replace(record, state=CaptureState.DELETED), None
            self._try_artifact_lease(lease_target)
            identity = lease_target.identity
            if staging is not None and public is not None:
                if staging.identity != public.identity or staging.nlink != 2 or public.nlink != 2:
                    raise _Tampered
                os.unlink(record.staging_name, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            elif staging is not None:
                try:
                    os.link(
                        record.staging_name,
                        record.public_name,
                        src_dir_fd=self._root_fd,
                        dst_dir_fd=self._root_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise _Tampered from exc
                linked = self._observe(
                    record.public_name,
                    identity,
                    valid_name=_PUBLIC_NAME_RE,
                )
                if linked is None or linked.nlink != 2:
                    if linked is not None:
                        os.close(linked.fd)
                    raise _Tampered
                os.close(linked.fd)
                os.unlink(record.staging_name, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            result = (
                replace(
                    record,
                    state=CaptureState.ABANDONED,
                    artifact_identity=identity,
                    retention_at=record.created_at,
                    next_attempt_at=self._wall_clock(),
                    size=lease_target.size,
                ),
                lease_target,
            )
            lease_transferred = True
            return result
        finally:
            for observed in (staging, public):
                if observed is not None and (
                    observed is not lease_target or not lease_transferred
                ):
                    os.close(observed.fd)

    def _deleting_record(self, record: CaptureLifecycleRecord) -> CaptureLifecycleRecord:
        if record.state is CaptureState.DELETING:
            return record
        nonce = secrets.token_hex(8)
        return replace(
            record,
            state=CaptureState.DELETING,
            deletion_nonce=nonce,
            quarantine_name=f".capture-quarantine-{record.capture_id}-{nonce}",
        )

    def _quarantine_delete(self, record: CaptureLifecycleRecord) -> int:
        expected = record.artifact_identity
        public: _ObservedArtifact | None = None
        quarantine: _ObservedArtifact | None = None
        try:
            public = self._observe(record.public_name, expected, valid_name=_PUBLIC_NAME_RE)
            quarantine = self._observe(
                record.quarantine_name,
                expected,
                valid_name=_QUARANTINE_NAME_RE,
            )
            if public is None and quarantine is None:
                return record.size
            lease_target = public or quarantine
            if lease_target is not None:
                self._try_artifact_lease(lease_target)
            if public is not None and quarantine is not None:
                if (
                    public.identity != quarantine.identity
                    or public.nlink != 2
                    or quarantine.nlink != 2
                ):
                    raise _Tampered
            elif public is not None:
                os.link(
                    record.public_name,
                    record.quarantine_name,
                    src_dir_fd=self._root_fd,
                    dst_dir_fd=self._root_fd,
                    follow_symlinks=False,
                )
                linked = self._observe(
                    record.quarantine_name,
                    expected,
                    valid_name=_QUARANTINE_NAME_RE,
                )
                if linked is None or linked.nlink != 2:
                    if linked is not None:
                        os.close(linked.fd)
                    raise _Tampered
                os.close(linked.fd)
            if public is not None:
                os.unlink(record.public_name, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            verified = self._observe(
                record.quarantine_name,
                expected,
                valid_name=_QUARANTINE_NAME_RE,
            )
            if verified is None or verified.nlink != 1:
                if verified is not None:
                    os.close(verified.fd)
                raise _Tampered
            os.close(verified.fd)
            os.unlink(record.quarantine_name, dir_fd=self._root_fd)
            os.fsync(self._root_fd)
            return record.size
        finally:
            if public is not None:
                os.close(public.fd)
            if quarantine is not None:
                os.close(quarantine.fd)

    def _retry(self, record: CaptureLifecycleRecord) -> CaptureLifecycleRecord:
        retry_count = record.retry_count + 1
        delay = min(float(2 ** min(retry_count, 12)), _MAX_RETRY_SECONDS)
        return replace(
            record,
            retry_count=retry_count,
            next_attempt_at=self._wall_clock() + delay,
        )

    def _sweep_one(self, capture_id: str) -> tuple[str, int, int]:
        with self._locked():
            records, generation, size = self._load_locked()
            record = records.get(capture_id)
            now = self._wall_clock()
            if (
                record is None
                or record.state in {CaptureState.DELETED, CaptureState.TAMPERED}
                or record.next_attempt_at > now
            ):
                return ("not_due", 0, 0)
            try:
                if record.state in {
                    CaptureState.RESERVED,
                    CaptureState.STAGED,
                    CaptureState.PUBLISHED_WRITING,
                } or (
                    record.state in {CaptureState.FINALIZED, CaptureState.FAILED}
                    and not record.deletion_nonce
                ):
                    record, lease = self._normalize_abandoned(record)
                    if lease is not None:
                        os.close(lease.fd)
                    if record.state is CaptureState.DELETED:
                        self._append_locked(record, records, generation, size)
                        return ("deleted", 0, 0)
                    records[record.capture_id] = record
                    self._append_locked(record, records, generation, size)
                    records, generation, size = self._load_locked()
                    record = records[capture_id]

                deleting = self._deleting_record(record)
                if deleting is not record:
                    records[record.capture_id] = deleting
                    self._append_locked(deleting, records, generation, size)
                    records, generation, size = self._load_locked()
                    record = records[capture_id]
                deleted_bytes = self._quarantine_delete(record)
                deleted = replace(
                    record,
                    state=CaptureState.DELETED,
                    next_attempt_at=now,
                )
                self._append_locked(deleted, records, generation, size)
                return ("deleted", deleted_bytes, 0)
            except _WriterLive:
                live = replace(record, next_attempt_at=now + 30.0)
                self._append_locked(live, records, generation, size)
                return ("writer_live", 0, 0)
            except _Tampered:
                tampered = replace(record, state=CaptureState.TAMPERED)
                self._append_locked(tampered, records, generation, size)
                return ("tampered", 0, 0)
            except (CaptureLifecycleError, OSError):
                retry = self._retry(record)
                self._append_locked(retry, records, generation, size)
                return ("error", 0, 1)

    def _due_ids(self, now: float) -> list[str]:
        with self._locked():
            records, _generation, _size = self._load_locked()
        due = [
            record
            for record in records.values()
            if record.state not in {CaptureState.DELETED, CaptureState.TAMPERED}
            and record.next_attempt_at <= now
        ]
        due.sort(key=lambda record: (record.next_attempt_at, record.capture_id))
        return [record.capture_id for record in due]

    def sweep(
        self,
        *,
        max_items: int = 32,
        max_duration_seconds: float = 0.05,
    ) -> CaptureCleanupOutcome:
        if max_items <= 0 or max_duration_seconds <= 0:
            raise CaptureLifecycleError("cleanup bounds must be positive")
        started = self._monotonic()
        examined = deleted = deleted_bytes = writer_live = 0
        not_due = tampered = errors = retry_count = 0
        for capture_id in self._due_ids(self._wall_clock())[:max_items]:
            if self._monotonic() - started >= max_duration_seconds:
                break
            examined += 1
            result, logical_bytes, retries = self._sweep_one(capture_id)
            deleted_bytes += logical_bytes
            retry_count += retries
            if result == "deleted":
                deleted += 1
            elif result == "writer_live":
                writer_live += 1
            elif result == "tampered":
                tampered += 1
            elif result == "error":
                errors += 1
            else:
                not_due += 1
        remaining_due = len(self._due_ids(self._wall_clock()))
        return CaptureCleanupOutcome(
            examined=examined,
            deleted=deleted,
            deleted_bytes=deleted_bytes,
            writer_live=writer_live,
            not_due=not_due,
            tampered=tampered,
            errors=errors,
            retry_count=retry_count,
            remaining_due=remaining_due,
            duration=max(0.0, self._monotonic() - started),
        )
