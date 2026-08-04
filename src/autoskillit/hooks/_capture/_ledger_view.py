"""Validated materialized view of the append-only capture lifecycle ledger."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import replace

from . import _ledger
from ._module_identity import register_module_aliases
from ._types import LedgerIncarnation, LedgerSnapshot

register_module_aliases(__name__)

Record = _ledger.CaptureLifecycleRecord
Records = dict[str, Record]
ReplayAccount = Callable[[int], None]
LegacyCompactor = Callable[
    [Mapping[str, Record], int, bytes, os.stat_result],
    None,
]


def identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def validate_control_file(
    fd: int,
    name: str,
    untrusted_write_bits: int,
    error: type[RuntimeError],
) -> None:
    value = os.fstat(fd)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or value.st_uid != os.geteuid()
        or value.st_mode & untrusted_write_bits
    ):
        raise error(f"unsafe lifecycle control file: {name}")


class LegacyCompacted(Exception):
    """Signals that the caller must reopen the atomically replaced ledger."""


def _read_range(fd: int, offset: int, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = os.pread(fd, min(64 * 1024, length - len(data)), offset + len(data))
        if not chunk:
            raise _ledger.LedgerCodecError("lifecycle ledger read ended early")
        data.extend(chunk)
    return bytes(data)


def _current_record(frame: _ledger.LedgerFrame) -> Record:
    return replace(
        _ledger.record_from_dict(frame.record),
        compaction_epoch=frame.compaction_epoch,
    )


def _decode_full(data: bytes) -> tuple[Records, int, bool, int | None]:
    decoded = _ledger.decode_ledger(data)
    records: Records = {}
    source_versions: dict[str, int] = {}
    compaction_epoch = 1
    saw_legacy = False
    for frame in decoded.frames:
        if frame.compaction_epoch < compaction_epoch:
            raise _ledger.LedgerCodecError("lifecycle compaction epoch regressed")
        compaction_epoch = frame.compaction_epoch
        raw_capture_id = frame.record.get("capture_id")
        previous = records.get(raw_capture_id) if isinstance(raw_capture_id, str) else None
        if frame.format_version == 1:
            if previous is not None and source_versions[previous.capture_id] != 1:
                raise _ledger.LedgerCodecError("legacy frame follows current lifecycle state")
            saw_legacy = True
            record = _ledger.legacy_record_from_dict(
                frame.record,
                revision=1 if previous is None else previous.revision + 1,
                compaction_epoch=frame.compaction_epoch,
            )
        else:
            record = _current_record(frame)
        if previous is not None and frame.format_version != 1:
            _ledger.validate_successor(previous, record)
        records[record.capture_id] = record
        source_versions[record.capture_id] = frame.format_version
    return records, compaction_epoch, saw_legacy, decoded.truncate_at


class LedgerView:
    """Cache whose identity is always subordinate to the validated ledger."""

    def __init__(self) -> None:
        self.records: Records | None = None
        self.incarnation: LedgerIncarnation | None = None
        self.snapshot: LedgerSnapshot | None = None

    def install(
        self,
        *,
        records: Records,
        compaction_epoch: int,
        value: os.stat_result,
        decoded_offset: int,
    ) -> tuple[Records, int, int]:
        self.records = records
        self.incarnation = LedgerIncarnation(
            value.st_dev,
            value.st_ino,
            compaction_epoch,
        )
        self.snapshot = LedgerSnapshot(
            size=value.st_size,
            ctime_ns=value.st_ctime_ns,
            decoded_offset=decoded_offset,
        )
        return records, compaction_epoch, value.st_size

    def note_append(
        self,
        records: Mapping[str, Record],
        record: Record,
        compaction_epoch: int,
        value: os.stat_result,
    ) -> None:
        latest = dict(records)
        latest[record.capture_id] = replace(record, compaction_epoch=compaction_epoch)
        self.install(
            records=latest,
            compaction_epoch=compaction_epoch,
            value=value,
            decoded_offset=value.st_size,
        )

    def note_compaction(
        self,
        records: Mapping[str, Record],
        compaction_epoch: int,
        value: os.stat_result,
    ) -> None:
        latest = {
            record.capture_id: replace(record, compaction_epoch=compaction_epoch)
            for record in records.values()
        }
        self.install(
            records=latest,
            compaction_epoch=compaction_epoch,
            value=value,
            decoded_offset=value.st_size,
        )

    def _load_full(
        self,
        fd: int,
        value: os.stat_result,
        *,
        account_replay: ReplayAccount,
        compact_legacy: LegacyCompactor,
    ) -> tuple[Records, int, int]:
        account_replay(value.st_size)
        source_bytes = _read_range(fd, 0, value.st_size)
        records, compaction_epoch, saw_legacy, truncate_at = _decode_full(source_bytes)
        decoded_offset = value.st_size
        if truncate_at is not None:
            os.ftruncate(fd, truncate_at)
            os.fsync(fd)
            decoded_offset = truncate_at
            value = os.fstat(fd)
            source_bytes = source_bytes[:truncate_at]
        if saw_legacy:
            compact_legacy(records, compaction_epoch + 1, source_bytes, value)
            raise LegacyCompacted
        return self.install(
            records=records,
            compaction_epoch=compaction_epoch,
            value=value,
            decoded_offset=decoded_offset,
        )

    def load(
        self,
        fd: int,
        *,
        max_ledger_bytes: int,
        account_replay: ReplayAccount,
        compact_legacy: LegacyCompactor,
    ) -> tuple[Records, int, int]:
        value = os.fstat(fd)
        if value.st_size > max_ledger_bytes:
            raise _ledger.LedgerCodecError("lifecycle ledger exceeds bound")
        records = self.records
        incarnation = self.incarnation
        snapshot = self.snapshot
        if records is None or incarnation is None or snapshot is None:
            return self._load_full(
                fd,
                value,
                account_replay=account_replay,
                compact_legacy=compact_legacy,
            )
        if (value.st_dev, value.st_ino) != (incarnation.device, incarnation.inode):
            return self._load_full(
                fd,
                value,
                account_replay=account_replay,
                compact_legacy=compact_legacy,
            )
        if value.st_size == snapshot.size:
            if value.st_ctime_ns == snapshot.ctime_ns:
                return records, incarnation.compaction_epoch, value.st_size
            return self._load_full(
                fd,
                value,
                account_replay=account_replay,
                compact_legacy=compact_legacy,
            )
        if value.st_size < snapshot.decoded_offset:
            return self._load_full(
                fd,
                value,
                account_replay=account_replay,
                compact_legacy=compact_legacy,
            )
        tail_size = value.st_size - snapshot.decoded_offset
        account_replay(tail_size)
        decoded = _ledger.decode_ledger(_read_range(fd, snapshot.decoded_offset, tail_size))
        if any(
            frame.format_version == 1 or frame.compaction_epoch != incarnation.compaction_epoch
            for frame in decoded.frames
        ):
            return self._load_full(
                fd,
                value,
                account_replay=account_replay,
                compact_legacy=compact_legacy,
            )
        latest = dict(records)
        for frame in decoded.frames:
            record = _current_record(frame)
            previous = latest.get(record.capture_id)
            if previous is not None:
                _ledger.validate_successor(previous, record)
            latest[record.capture_id] = record
        decoded_offset = value.st_size
        if decoded.truncate_at is not None:
            decoded_offset = snapshot.decoded_offset + decoded.truncate_at
            os.ftruncate(fd, decoded_offset)
            os.fsync(fd)
            value = os.fstat(fd)
        return self.install(
            records=latest,
            compaction_epoch=incarnation.compaction_epoch,
            value=value,
            decoded_offset=decoded_offset,
        )
