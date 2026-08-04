"""Crash-recoverable capacity migration for legacy capture ledgers."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from . import (
    _capacity,
    _control_file,
    _ledger,
    _ledger_view,
    _store_port,
    _sweep,
    _sweep_cursor,
)
from ._failure_policy import CaptureFailureReason
from ._module_identity import register_module_aliases
from ._types import CaptureCapacitySpec, DueKey, SweepBudgetSpec

register_module_aliases(__name__)

MIGRATION_NAME = ".capture-legacy-migration"

_VERSION = 1
_MAX_ENTRIES = 4096
_MAX_BYTES = 1024 * 1024
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW

Record = _ledger.CaptureLifecycleRecord
Records = dict[str, Record]


class MigrationPhase(StrEnum):
    PLANNED = "PLANNED"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


class MigrationBlockedError(RuntimeError):
    def __init__(self, detail: str = "legacy migration is externally blocked") -> None:
        self.reason = CaptureFailureReason.MIGRATION_BLOCKED
        super().__init__(detail)


class MigrationIntegrityError(RuntimeError):
    failure_reason = CaptureFailureReason.LEDGER_INTEGRITY


class MigrationAuthorityError(OSError):
    def __init__(self, detail: str, *, error_number: int | None = errno.ELOOP) -> None:
        super().__init__(error_number, detail)


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    due_key: DueKey
    phase: MigrationPhase
    deletion_nonce: str

    def __post_init__(self) -> None:
        if (
            type(self.due_key) is not DueKey
            or type(self.phase) is not MigrationPhase
            or not isinstance(self.deletion_nonce, str)
            or len(self.deletion_nonce) != 16
            or any(character not in "0123456789abcdef" for character in self.deletion_nonce)
        ):
            raise MigrationIntegrityError("invalid legacy migration entry")


@dataclass(frozen=True, slots=True)
class LegacyMigrationTxn:
    source_device: int
    source_inode: int
    source_length: int
    source_digest: str
    normalized_view_digest: str
    target_epoch: int
    cursor: DueKey | None = None
    entries: tuple[MigrationEntry, ...] = ()
    publication_digest: str = ""

    def __post_init__(self) -> None:
        integers = (
            self.source_device,
            self.source_inode,
            self.source_length,
            self.target_epoch,
        )
        if (
            any(type(value) is not int or value < 0 for value in integers)
            or self.target_epoch < 1
            or any(
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
                for value in (self.source_digest, self.normalized_view_digest)
            )
            or (
                self.publication_digest
                and (
                    len(self.publication_digest) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in self.publication_digest
                    )
                )
            )
            or (self.cursor is not None and type(self.cursor) is not DueKey)
            or len(self.entries) > _MAX_ENTRIES
            or len({entry.due_key.capture_id for entry in self.entries}) != len(self.entries)
        ):
            raise MigrationIntegrityError("invalid legacy migration transaction")

    def entry_for(self, capture_id: str) -> MigrationEntry | None:
        return next(
            (entry for entry in self.entries if entry.due_key.capture_id == capture_id),
            None,
        )

    def with_entry(self, entry: MigrationEntry) -> LegacyMigrationTxn:
        entries = tuple(
            sorted(
                (
                    *(
                        item
                        for item in self.entries
                        if item.due_key.capture_id != entry.due_key.capture_id
                    ),
                    entry,
                ),
                key=lambda item: item.due_key.capture_id,
            )
        )
        return replace(self, cursor=entry.due_key, entries=entries)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )


def _due_primitive(value: DueKey | None) -> list[object] | None:
    return None if value is None else [value.next_attempt_at, value.capture_id]


def _primitive(txn: LegacyMigrationTxn) -> dict[str, object]:
    return {
        "cursor": _due_primitive(txn.cursor),
        "entries": [
            {
                "deletion_nonce": entry.deletion_nonce,
                "due_key": _due_primitive(entry.due_key),
                "phase": entry.phase.value,
            }
            for entry in txn.entries
        ],
        "normalized_view_digest": txn.normalized_view_digest,
        "publication_digest": txn.publication_digest,
        "source_device": txn.source_device,
        "source_digest": txn.source_digest,
        "source_inode": txn.source_inode,
        "source_length": txn.source_length,
        "target_epoch": txn.target_epoch,
        "version": _VERSION,
    }


def _parse_due(value: object) -> DueKey | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise MigrationIntegrityError("invalid legacy migration cursor")
    return DueKey(value[0], value[1])


def _parse(payload: bytes) -> LegacyMigrationTxn:
    if len(payload) > _MAX_BYTES:
        raise MigrationIntegrityError("legacy migration transaction exceeds bound")
    try:
        value = json.loads(payload)
        if not isinstance(value, dict) or _canonical(value) != payload:
            raise MigrationIntegrityError("legacy migration transaction is not canonical")
        expected = {
            "cursor",
            "entries",
            "normalized_view_digest",
            "publication_digest",
            "source_device",
            "source_digest",
            "source_inode",
            "source_length",
            "target_epoch",
            "version",
        }
        if set(value) != expected or value["version"] != _VERSION:
            raise MigrationIntegrityError("legacy migration transaction fields do not match")
        raw_entries = value["entries"]
        if not isinstance(raw_entries, list) or len(raw_entries) > _MAX_ENTRIES:
            raise MigrationIntegrityError("legacy migration entry bound exceeded")
        parsed_entries: list[MigrationEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict) or set(item) != {
                "deletion_nonce",
                "due_key",
                "phase",
            }:
                raise MigrationIntegrityError("invalid legacy migration entries")
            due_key = _parse_due(item["due_key"])
            if due_key is None:
                raise MigrationIntegrityError("invalid legacy migration entries")
            parsed_entries.append(
                MigrationEntry(
                    due_key=due_key,
                    phase=MigrationPhase(item["phase"]),
                    deletion_nonce=item["deletion_nonce"],
                )
            )
        entries = tuple(parsed_entries)
        return LegacyMigrationTxn(
            source_device=value["source_device"],
            source_inode=value["source_inode"],
            source_length=value["source_length"],
            source_digest=value["source_digest"],
            normalized_view_digest=value["normalized_view_digest"],
            target_epoch=value["target_epoch"],
            cursor=_parse_due(value["cursor"]),
            entries=entries,
            publication_digest=value["publication_digest"],
        )
    except MigrationIntegrityError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationIntegrityError("invalid legacy migration transaction") from exc


def _validate_file(value: os.stat_result) -> None:
    _control_file.validate_private_file(
        value,
        MigrationAuthorityError("unsafe legacy migration transaction"),
    )


def load_transaction(root_fd: int) -> LegacyMigrationTxn | None:
    try:
        observed = os.stat(MIGRATION_NAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MigrationAuthorityError(
            "cannot inspect legacy migration transaction",
            error_number=exc.errno,
        ) from exc
    _validate_file(observed)
    try:
        fd = os.open(MIGRATION_NAME, _READ_FLAGS, dir_fd=root_fd)
    except OSError as exc:
        raise MigrationAuthorityError(
            "cannot open legacy migration transaction",
            error_number=exc.errno,
        ) from exc
    try:
        current = os.fstat(fd)
        _validate_file(current)
        if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
            raise MigrationAuthorityError("legacy migration transaction identity changed")
        payload = bytearray()
        while len(payload) <= _MAX_BYTES:
            chunk = os.read(fd, min(64 * 1024, _MAX_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
    finally:
        os.close(fd)
    return _parse(bytes(payload))


def write_transaction(root_fd: int, txn: LegacyMigrationTxn) -> None:
    existing = load_transaction(root_fd)
    if existing is not None and (
        existing.source_device,
        existing.source_inode,
        existing.source_length,
        existing.source_digest,
        existing.normalized_view_digest,
        existing.target_epoch,
    ) != (
        txn.source_device,
        txn.source_inode,
        txn.source_length,
        txn.source_digest,
        txn.normalized_view_digest,
        txn.target_epoch,
    ):
        raise MigrationIntegrityError("legacy migration source binding changed")
    payload = _canonical(_primitive(txn))
    if len(payload) > _MAX_BYTES:
        raise MigrationIntegrityError("legacy migration transaction exceeds bound")
    _control_file.publish_private_file(
        root_fd,
        target_name=MIGRATION_NAME,
        temp_prefix=".capture-legacy-migration-",
        payload=payload,
        validate_file=_validate_file,
        write_all=_ledger.write_all,
    )


def remove_transaction(root_fd: int) -> None:
    if load_transaction(root_fd) is None:
        return
    os.unlink(MIGRATION_NAME, dir_fd=root_fd)
    os.fsync(root_fd)


def _frames_digest(
    records: Mapping[str, Record],
    epoch: int,
    spec: CaptureCapacitySpec,
) -> str:
    digest = hashlib.sha256()
    for record in _capacity.compacted_records(records, spec):
        digest.update(_ledger.encode_frame(_ledger.record_to_dict(record), compaction_epoch=epoch))
    return digest.hexdigest()


def new_transaction(
    records: Mapping[str, Record],
    *,
    target_epoch: int,
    source_bytes: bytes,
    source_stat: os.stat_result,
    spec: CaptureCapacitySpec,
) -> LegacyMigrationTxn:
    return LegacyMigrationTxn(
        source_device=source_stat.st_dev,
        source_inode=source_stat.st_ino,
        source_length=len(source_bytes),
        source_digest=hashlib.sha256(source_bytes).hexdigest(),
        normalized_view_digest=_frames_digest(records, target_epoch, spec),
        target_epoch=target_epoch,
    )


def load_ledger(
    store: _store_port.MigrationStorePort,
    fd: int,
    view: _ledger_view.LedgerView,
    *,
    max_ledger_bytes: int,
) -> tuple[Records, int, int]:
    spec = store._capacity
    max_attempts = (store._sweep_budget or SweepBudgetSpec()).max_attempts

    def migrate(
        records: Mapping[str, Record],
        target_epoch: int,
        source_bytes: bytes,
        source_stat: os.stat_result,
    ) -> None:
        migrate_legacy(
            store,
            records,
            target_epoch=target_epoch,
            source_bytes=source_bytes,
            source_stat=source_stat,
            spec=spec,
            max_attempts=max_attempts,
        )

    loaded = view.load(
        fd,
        max_ledger_bytes=max_ledger_bytes,
        account_replay=lambda amount: _sweep.account_replay_bytes(store, amount),
        compact_legacy=migrate,
    )
    records, epoch, _size = loaded
    finish_published(store, records, epoch, spec)
    return loaded


def _validate_source(
    txn: LegacyMigrationTxn,
    records: Mapping[str, Record],
    source_bytes: bytes,
    source_stat: os.stat_result,
    spec: CaptureCapacitySpec,
) -> None:
    if (
        txn.source_device != source_stat.st_dev
        or txn.source_inode != source_stat.st_ino
        or txn.source_length != len(source_bytes)
        or txn.source_digest != hashlib.sha256(source_bytes).hexdigest()
        or txn.normalized_view_digest != _frames_digest(records, txn.target_epoch, spec)
    ):
        raise MigrationIntegrityError("legacy migration source binding changed")


def _overlay(records: Mapping[str, Record], txn: LegacyMigrationTxn) -> Records:
    result = dict(records)
    for entry in txn.entries:
        if entry.phase is not MigrationPhase.RETIRED:
            continue
        record = result.get(entry.due_key.capture_id)
        if record is not None and record.state is not _ledger.CaptureState.DELETED:
            result[record.capture_id] = replace(
                record,
                state=_ledger.CaptureState.DELETED,
                retention_phase=_ledger.CaptureRetentionPhase.DELETED,
                next_attempt_at=entry.due_key.next_attempt_at,
                revision=record.revision + 1,
            )
    return result


def _fits(records: Mapping[str, Record], epoch: int, spec: CaptureCapacitySpec) -> bool:
    encoded = _capacity.compacted_bytes(records, epoch, spec)
    return (
        encoded <= spec.compaction_low_bytes
        and encoded + _capacity.recovery_headroom(spec) <= spec.hard_ledger_bytes
    )


def _publish(
    store: _store_port.MigrationStorePort,
    txn: LegacyMigrationTxn,
    records: Records,
    spec: CaptureCapacitySpec,
) -> None:
    publication_digest = _frames_digest(records, txn.target_epoch, spec)
    txn = replace(txn, publication_digest=publication_digest)
    write_transaction(store._root_fd, txn)
    store._compact_locked(records, txn.target_epoch)
    due = sorted(
        DueKey(record.next_attempt_at, record.capture_id)
        for record in records.values()
        if record.state not in {_ledger.CaptureState.DELETED, _ledger.CaptureState.TAMPERED}
    )
    if due:
        _sweep_cursor.write_cursor(
            store._root_fd,
            project_identity=store._project_identity,
            root_identity=store._root_identity,
            compaction_epoch=txn.target_epoch,
            due_key=due[-1],
        )
    remove_transaction(store._root_fd)


def migrate_legacy(
    store: _store_port.MigrationStorePort,
    records: Mapping[str, Record],
    *,
    target_epoch: int,
    source_bytes: bytes,
    source_stat: os.stat_result,
    spec: CaptureCapacitySpec,
    max_attempts: int,
) -> None:
    txn = load_transaction(store._root_fd)
    if txn is None:
        txn = new_transaction(
            records,
            target_epoch=target_epoch,
            source_bytes=source_bytes,
            source_stat=source_stat,
            spec=spec,
        )
        write_transaction(store._root_fd, txn)
    else:
        _validate_source(txn, records, source_bytes, source_stat, spec)
    assert txn is not None
    projected = _overlay(records, txn)
    if _fits(projected, txn.target_epoch, spec):
        _publish(store, txn, projected, spec)
        return
    candidates = sorted(
        DueKey(record.next_attempt_at, record.capture_id)
        for record in projected.values()
        if record.state not in {_ledger.CaptureState.DELETED, _ledger.CaptureState.TAMPERED}
        and record.next_attempt_at <= store._wall_clock()
    )
    candidates = _sweep_cursor.rotate_after(candidates, txn.cursor)
    attempts = 0
    for due_key in candidates:
        if attempts >= max_attempts:
            break
        attempts += 1
        entry = txn.entry_for(due_key.capture_id)
        if entry is None or entry.phase is MigrationPhase.PLANNED:
            entry = MigrationEntry(
                due_key=due_key,
                phase=MigrationPhase.PLANNED,
                deletion_nonce=(
                    entry.deletion_nonce if entry is not None else secrets.token_hex(8)
                ),
            )
            txn = txn.with_entry(entry)
            write_transaction(store._root_fd, txn)
        assert entry is not None
        if entry.phase is MigrationPhase.RETIRED:
            continue
        record = projected[due_key.capture_id]
        lease = None
        try:
            normalized = record
            if entry.phase is MigrationPhase.PLANNED:
                normalized, lease = store._normalize_abandoned(record)
                if normalized.state is _ledger.CaptureState.DELETED:
                    txn = txn.with_entry(replace(entry, phase=MigrationPhase.RETIRED))
                    write_transaction(store._root_fd, txn)
                    projected = _overlay(records, txn)
                    if _fits(projected, txn.target_epoch, spec):
                        _publish(store, txn, projected, spec)
                        return
                    continue
            deleting = _sweep.deleting_record(normalized, nonce=entry.deletion_nonce)
            active_entry: MigrationEntry = entry
            active_txn: LegacyMigrationTxn = txn

            def authorize() -> None:
                nonlocal active_entry, active_txn
                active_entry = replace(active_entry, phase=MigrationPhase.QUARANTINED)
                active_txn = active_txn.with_entry(active_entry)
                write_transaction(store._root_fd, active_txn)

            store._quarantine_delete(
                deleting,
                authorize_delete=authorize if entry.phase is MigrationPhase.PLANNED else None,
                preleased=lease,
                lease_checked=lease is not None,
            )
            entry, txn = active_entry, active_txn
            txn = txn.with_entry(replace(entry, phase=MigrationPhase.RETIRED))
            write_transaction(store._root_fd, txn)
            projected = _overlay(records, txn)
            if _fits(projected, txn.target_epoch, spec):
                _publish(store, txn, projected, spec)
                return
        except (_sweep.CarrierLeaseLive, _sweep.Tampered):
            continue
        finally:
            if lease is not None:
                os.close(lease.fd)
    raise MigrationBlockedError()


def finish_published(
    store: _store_port.MigrationStorePort,
    records: Mapping[str, Record],
    epoch: int,
    spec: CaptureCapacitySpec,
) -> None:
    txn = load_transaction(store._root_fd)
    if txn is None:
        return
    if (
        not txn.publication_digest
        or epoch != txn.target_epoch
        or _frames_digest(records, epoch, spec) != txn.publication_digest
    ):
        raise MigrationIntegrityError("published ledger does not match migration transaction")
    due = sorted(
        DueKey(record.next_attempt_at, record.capture_id)
        for record in records.values()
        if record.state not in {_ledger.CaptureState.DELETED, _ledger.CaptureState.TAMPERED}
    )
    if due:
        _sweep_cursor.write_cursor(
            store._root_fd,
            project_identity=store._project_identity,
            root_identity=store._root_identity,
            compaction_epoch=epoch,
            due_key=due[-1],
        )
    remove_transaction(store._root_fd)
