"""Validated materialized view of the append-only capture lifecycle ledger."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from . import _capacity, _ledger
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


@dataclass(frozen=True, slots=True)
class _DecodedView:
    records: Records
    frame_sizes: Mapping[str, tuple[int, int]]
    compaction_epoch: int
    saw_legacy: bool
    truncate_at: int | None
    opaque_frames: tuple[bytes, ...]
    opaque_capture_ids: frozenset[str]


def _decode_full(data: bytes) -> _DecodedView:
    decoded = _ledger.decode_ledger(data)
    records: Records = {}
    frame_sizes: dict[str, tuple[int, int]] = {}
    source_versions: dict[str, int] = {}
    opaque_frames: list[bytes] = []
    opaque_capture_ids: set[str] = set()
    compaction_epoch = 1
    saw_legacy = False
    for frame in decoded.frames:
        if frame.compaction_epoch < compaction_epoch:
            raise _ledger.LedgerCodecError("lifecycle compaction epoch regressed")
        compaction_epoch = frame.compaction_epoch
        raw_capture_id = frame.record.get("capture_id")
        if isinstance(raw_capture_id, str) and raw_capture_id in opaque_capture_ids:
            opaque_frames.append(frame.exact_bytes)
            continue
        previous = records.get(raw_capture_id) if isinstance(raw_capture_id, str) else None
        if (
            frame.format_version == 1
            and previous is not None
            and source_versions[previous.capture_id] != 1
        ):
            raise _ledger.LedgerCodecError("legacy frame follows current lifecycle state")
        try:
            if frame.format_version == 1:
                record = _ledger.legacy_record_from_dict(
                    frame.record,
                    revision=1 if previous is None else previous.revision + 1,
                    compaction_epoch=frame.compaction_epoch,
                )
                saw_legacy = True
            else:
                record = _current_record(frame)
        except _ledger.LedgerCodecError:
            opaque_frames.append(frame.exact_bytes)
            if isinstance(raw_capture_id, str):
                opaque_capture_ids.add(raw_capture_id)
                records.pop(raw_capture_id, None)
                frame_sizes.pop(raw_capture_id, None)
                source_versions.pop(raw_capture_id, None)
            continue
        if previous is not None and frame.format_version != 1:
            _ledger.validate_successor(previous, record)
        records[record.capture_id] = record
        if frame.format_version != 1:
            frame_sizes[record.capture_id] = (len(frame.exact_bytes), frame.compaction_epoch)
        source_versions[record.capture_id] = frame.format_version
    return _DecodedView(
        records=records,
        frame_sizes=frame_sizes,
        compaction_epoch=compaction_epoch,
        saw_legacy=saw_legacy,
        truncate_at=decoded.truncate_at,
        opaque_frames=tuple(opaque_frames),
        opaque_capture_ids=frozenset(opaque_capture_ids),
    )


class LedgerView:
    """Cache whose identity is always subordinate to the validated ledger."""

    def __init__(self) -> None:
        self.records: Records | None = None
        self._sizer = _capacity.CompactedFrameSizer()
        self.incarnation: LedgerIncarnation | None = None
        self.snapshot: LedgerSnapshot | None = None
        self.opaque_frames: tuple[bytes, ...] = ()
        self.opaque_capture_ids: frozenset[str] = frozenset()

    @property
    def sizer(self) -> _capacity.CompactedFrameSizer:
        return self._sizer

    def install(
        self,
        *,
        records: Records,
        compaction_epoch: int,
        value: os.stat_result,
        decoded_offset: int,
        opaque_frames: tuple[bytes, ...],
        opaque_capture_ids: frozenset[str],
        frame_sizes: Mapping[str, int],
        sizes_are_complete: bool,
    ) -> tuple[Records, int, int]:
        self.records = records
        self.opaque_frames = opaque_frames
        self.opaque_capture_ids = opaque_capture_ids
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
        if sizes_are_complete:
            self._sizer.reset()
        else:
            self._sizer.retain_records(records)
        self._sizer.hydrate(records, compaction_epoch, frame_sizes)
        return records, compaction_epoch, value.st_size

    def note_append(
        self,
        records: Mapping[str, Record],
        record: Record,
        compaction_epoch: int,
        value: os.stat_result,
        frame: bytes,
    ) -> None:
        latest = dict(records)
        latest[record.capture_id] = replace(record, compaction_epoch=compaction_epoch)
        self.install(
            records=latest,
            compaction_epoch=compaction_epoch,
            value=value,
            decoded_offset=value.st_size,
            opaque_frames=self.opaque_frames,
            opaque_capture_ids=self.opaque_capture_ids,
            frame_sizes={record.capture_id: len(frame)},
            sizes_are_complete=False,
        )

    def note_compaction(
        self,
        records: Mapping[str, Record],
        compaction_epoch: int,
        value: os.stat_result,
        actionable_frames: Mapping[str, bytes],
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
            opaque_frames=self.opaque_frames,
            opaque_capture_ids=self.opaque_capture_ids,
            frame_sizes={
                capture_id: len(frame) for capture_id, frame in actionable_frames.items()
            },
            sizes_are_complete=True,
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
        decoded = _decode_full(source_bytes)
        decoded_offset = value.st_size
        if decoded.truncate_at is not None:
            os.ftruncate(fd, decoded.truncate_at)
            os.fsync(fd)
            decoded_offset = decoded.truncate_at
            value = os.fstat(fd)
            source_bytes = source_bytes[: decoded.truncate_at]
        if decoded.saw_legacy:
            self.opaque_frames = decoded.opaque_frames
            self.opaque_capture_ids = decoded.opaque_capture_ids
            compact_legacy(
                decoded.records,
                decoded.compaction_epoch + 1,
                source_bytes,
                value,
            )
            raise LegacyCompacted
        return self.install(
            records=decoded.records,
            compaction_epoch=decoded.compaction_epoch,
            value=value,
            decoded_offset=decoded_offset,
            opaque_frames=decoded.opaque_frames,
            opaque_capture_ids=decoded.opaque_capture_ids,
            frame_sizes={
                capture_id: size
                for capture_id, (size, frame_epoch) in decoded.frame_sizes.items()
                if frame_epoch == decoded.compaction_epoch
            },
            sizes_are_complete=True,
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
        opaque_frames = list(self.opaque_frames)
        opaque_capture_ids = set(self.opaque_capture_ids)
        frame_sizes: dict[str, int] = {}
        for frame in decoded.frames:
            raw_capture_id = frame.record.get("capture_id")
            if isinstance(raw_capture_id, str) and raw_capture_id in opaque_capture_ids:
                opaque_frames.append(frame.exact_bytes)
                continue
            try:
                record = _current_record(frame)
            except _ledger.LedgerCodecError:
                opaque_frames.append(frame.exact_bytes)
                if isinstance(raw_capture_id, str):
                    opaque_capture_ids.add(raw_capture_id)
                    latest.pop(raw_capture_id, None)
                    frame_sizes.pop(raw_capture_id, None)
                continue
            previous = latest.get(record.capture_id)
            if previous is not None:
                _ledger.validate_successor(previous, record)
            latest[record.capture_id] = record
            frame_sizes[record.capture_id] = len(frame.exact_bytes)
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
            opaque_frames=tuple(opaque_frames),
            opaque_capture_ids=frozenset(opaque_capture_ids),
            frame_sizes=frame_sizes,
            sizes_are_complete=False,
        )
