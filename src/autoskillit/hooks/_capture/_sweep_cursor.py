"""Descriptor-authorized durable fairness cursor for lifecycle sweeps."""

from __future__ import annotations

import errno
import json
import math
import os
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from . import _control_file, _ledger, _store_port
from ._module_identity import register_module_aliases
from ._types import CleanupBlocker, DueKey, SweepBudgetExceeded, SweepBudgetSpec

register_module_aliases(__name__)

CURSOR_NAME = ".capture-sweep-cursor"

_MAX_CURSOR_BYTES = 1024
_VERSION = 1
_NOFOLLOW = os.O_NOFOLLOW
_CLOEXEC = os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | _CLOEXEC | _NOFOLLOW


class CursorAuthorityError(OSError):
    pass


class CursorStatus(StrEnum):
    MISSING = "missing"
    VALID = "valid"
    CONTENT_INVALID = "content_invalid"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class CursorLoad:
    status: CursorStatus
    due_key: DueKey | None = None


class SweepRecord(Protocol):
    @property
    def capture_id(self) -> str: ...

    @property
    def state(self) -> object: ...

    @property
    def next_attempt_at(self) -> float: ...


def _validate_file(value: os.stat_result) -> None:
    _control_file.validate_private_file(
        value,
        CursorAuthorityError(errno.ELOOP, "unsafe lifecycle sweep cursor"),
    )


def _observe(root_fd: int) -> os.stat_result | None:
    try:
        value = os.stat(CURSOR_NAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CursorAuthorityError(
            exc.errno,
            "cannot inspect lifecycle sweep cursor",
        ) from exc
    _validate_file(value)
    return value


def _read_all(fd: int) -> bytes:
    payload = bytearray()
    while len(payload) <= _MAX_CURSOR_BYTES:
        chunk = os.read(fd, _MAX_CURSOR_BYTES + 1 - len(payload))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
    return bytes(payload)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def load_cursor(
    root_fd: int,
    *,
    project_identity: tuple[int, int],
    root_identity: tuple[int, int],
    compaction_epoch: int,
) -> CursorLoad:
    observed = _observe(root_fd)
    if observed is None:
        return CursorLoad(CursorStatus.MISSING)
    try:
        fd = os.open(CURSOR_NAME, _READ_FLAGS, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CursorAuthorityError(
                exc.errno,
                "unsafe lifecycle sweep cursor",
            ) from exc
        raise CursorAuthorityError(
            exc.errno,
            "cannot open lifecycle sweep cursor",
        ) from exc
    try:
        current = os.fstat(fd)
        _validate_file(current)
        if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
            raise CursorAuthorityError(
                errno.ELOOP,
                "lifecycle sweep cursor identity changed",
            )
        payload = _read_all(fd)
    finally:
        os.close(fd)
    if len(payload) > _MAX_CURSOR_BYTES:
        return CursorLoad(CursorStatus.CONTENT_INVALID)
    try:
        decoded = json.loads(payload)
        if (
            not isinstance(decoded, dict)
            or set(decoded)
            != {
                "capture_id",
                "compaction_epoch",
                "next_attempt_at",
                "project_identity",
                "root_identity",
                "version",
            }
            or _canonical(decoded) != payload
            or decoded["version"] != _VERSION
            or not isinstance(decoded["capture_id"], str)
            or not decoded["capture_id"]
            or not isinstance(decoded["next_attempt_at"], (int, float))
            or isinstance(decoded["next_attempt_at"], bool)
            or not math.isfinite(decoded["next_attempt_at"])
            or type(decoded["compaction_epoch"]) is not int
            or decoded["compaction_epoch"] < 1
            or decoded["project_identity"] != list(project_identity)
            or decoded["root_identity"] != list(root_identity)
        ):
            return CursorLoad(CursorStatus.CONTENT_INVALID)
        due_key = DueKey(decoded["next_attempt_at"], decoded["capture_id"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return CursorLoad(CursorStatus.CONTENT_INVALID)
    if decoded["compaction_epoch"] != compaction_epoch:
        return CursorLoad(CursorStatus.STALE)
    return CursorLoad(CursorStatus.VALID, due_key)


def _write_payload(fd: int, payload: bytes) -> None:
    try:
        _ledger.write_all(fd, payload)
    except _ledger.LedgerCodecError as exc:
        raise CursorAuthorityError(
            errno.EIO,
            "lifecycle sweep cursor write made no progress",
        ) from exc


def write_cursor(
    root_fd: int,
    *,
    project_identity: tuple[int, int],
    root_identity: tuple[int, int],
    compaction_epoch: int,
    due_key: DueKey,
) -> None:
    _observe(root_fd)
    payload = _canonical(
        {
            "capture_id": due_key.capture_id,
            "compaction_epoch": compaction_epoch,
            "next_attempt_at": due_key.next_attempt_at,
            "project_identity": list(project_identity),
            "root_identity": list(root_identity),
            "version": _VERSION,
        }
    )
    if len(payload) > _MAX_CURSOR_BYTES:
        raise CursorAuthorityError("lifecycle sweep cursor exceeds bound")
    _control_file.publish_private_file(
        root_fd,
        target_name=CURSOR_NAME,
        temp_prefix=".capture-sweep-cursor-",
        payload=payload,
        validate_file=_validate_file,
        write_all=_write_payload,
    )


def clear_cursor(root_fd: int) -> bool:
    if _observe(root_fd) is None:
        return False
    try:
        os.unlink(CURSOR_NAME, dir_fd=root_fd)
    except OSError as exc:
        raise CursorAuthorityError(
            exc.errno,
            "cannot remove lifecycle sweep cursor",
        ) from exc
    os.fsync(root_fd)
    return True


def rotate_after(keys: list[DueKey], cursor: DueKey | None) -> list[DueKey]:
    if cursor is None or not keys:
        return keys
    for index, key in enumerate(keys):
        if key > cursor:
            return keys[index:] + keys[:index]
    return keys


def is_due_record(
    record: SweepRecord,
    now: float,
    terminal_states: Collection[object],
) -> bool:
    """Return whether one non-terminal lifecycle record is due."""
    return record.state not in terminal_states and record.next_attempt_at <= now


def count_due_records(
    records: Iterable[SweepRecord],
    now: float,
    terminal_states: Collection[object],
) -> int:
    """Count due records without sweep ordering or materialization."""
    return sum(is_due_record(record, now, terminal_states) for record in records)


def bounded_due_keys(
    records: Iterable[SweepRecord],
    now: float,
    terminal_states: Collection[object],
    max_records: int,
) -> tuple[list[DueKey], bool, int, DueKey | None]:
    due: list[DueKey] = []
    inspected = 0
    complete = True
    rebuild_key: DueKey | None = None
    for record in records:
        if inspected >= max_records:
            complete = False
            break
        inspected += 1
        if record.state in terminal_states:
            continue
        key = DueKey(record.next_attempt_at, record.capture_id)
        rebuild_key = key if rebuild_key is None else max(rebuild_key, key)
        if is_due_record(record, now, terminal_states):
            due.append(key)
    due.sort()
    return due, complete, inspected, rebuild_key


def select_due_keys(
    store: _store_port.SweepStorePort,
    now: float,
    max_records: int,
    terminal_states: Collection[object],
) -> tuple[list[DueKey], bool, bool]:
    with store._locked():
        records, compaction_epoch, _size = store._load_locked()
        due, complete, inspected, rebuild_key = bounded_due_keys(
            records.values(),
            now,
            terminal_states,
            max_records,
        )
        cursor = load_cursor(
            store._root_fd,
            project_identity=store._project_identity,
            root_identity=store._root_identity,
            compaction_epoch=compaction_epoch,
        )
        repair_needed = cursor.status is not CursorStatus.VALID
        repaired = False
        if repair_needed and complete and not due:
            budget = store._sweep_budget
            if budget is None:
                raise RuntimeError("cursor repair requires an active sweep budget")
            if store._sweep_cursor_writes >= budget.max_cursor_writes:
                raise SweepBudgetExceeded(CleanupBlocker.CURSOR_WRITE_BUDGET)
            if rebuild_key is None:
                repaired = clear_cursor(store._root_fd)
            else:
                write_cursor(
                    store._root_fd,
                    project_identity=store._project_identity,
                    root_identity=store._root_identity,
                    compaction_epoch=compaction_epoch,
                    due_key=rebuild_key,
                )
                repaired = True
            if repaired:
                store._sweep_cursor_writes += 1
    store._sweep_records_inspected += inspected
    return (
        rotate_after(due, cursor.due_key),
        complete,
        repaired or (repair_needed and bool(due)),
    )


def advance_cursor(
    store: _store_port.SweepStorePort,
    due_key: DueKey,
    budget: SweepBudgetSpec,
) -> None:
    if store._sweep_cursor_writes >= budget.max_cursor_writes:
        raise SweepBudgetExceeded(CleanupBlocker.CURSOR_WRITE_BUDGET)
    with store._locked():
        _records, compaction_epoch, _size = store._load_locked()
        write_cursor(
            store._root_fd,
            project_identity=store._project_identity,
            root_identity=store._root_identity,
            compaction_epoch=compaction_epoch,
            due_key=due_key,
        )
    store._sweep_cursor_writes += 1


def account_replay_bytes(store: _store_port.SweepStorePort, amount: int) -> None:
    budget = store._sweep_budget
    if budget is not None and store._sweep_replay_bytes + amount > budget.max_replay_bytes:
        raise SweepBudgetExceeded(CleanupBlocker.REPLAY_BYTE_BUDGET)
    if budget is not None:
        store._sweep_replay_bytes += amount


def write_cursor_accounted(
    store: _store_port.SweepStorePort,
    *,
    compaction_epoch: int,
    due_key: DueKey,
) -> None:
    budget = store._sweep_budget
    if budget is not None and store._sweep_cursor_writes >= budget.max_cursor_writes:
        raise SweepBudgetExceeded(CleanupBlocker.CURSOR_WRITE_BUDGET)
    write_cursor(
        store._root_fd,
        project_identity=store._project_identity,
        root_identity=store._root_identity,
        compaction_epoch=compaction_epoch,
        due_key=due_key,
    )
    if budget is not None:
        store._sweep_cursor_writes += 1
