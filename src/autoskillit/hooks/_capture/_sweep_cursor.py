"""Descriptor-authorized durable fairness cursor for lifecycle sweeps."""

from __future__ import annotations

import errno
import json
import math
import os
import secrets
import stat
from dataclasses import dataclass
from enum import StrEnum

from ._module_identity import register_module_aliases
from ._types import DueKey

register_module_aliases(__name__)

CURSOR_NAME = ".capture-sweep-cursor"

_MAX_CURSOR_BYTES = 1024
_VERSION = 1
_NOFOLLOW = os.O_NOFOLLOW
_CLOEXEC = os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | _CLOEXEC | _NOFOLLOW
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW


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


def _validate_file(value: os.stat_result) -> None:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_nlink != 1
    ):
        raise CursorAuthorityError("unsafe lifecycle sweep cursor")


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
            raise CursorAuthorityError("lifecycle sweep cursor identity changed")
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


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise CursorAuthorityError("lifecycle sweep cursor write made no progress")
        offset += written


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
    temp_name = f".capture-sweep-cursor-{secrets.token_hex(8)}"
    fd = os.open(temp_name, _WRITE_FLAGS, 0o600, dir_fd=root_fd)
    try:
        _validate_file(os.fstat(fd))
        _write_all(fd, payload)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(temp_name, dir_fd=root_fd)
        except OSError:
            pass
        raise
    else:
        os.close(fd)
    try:
        os.replace(
            temp_name,
            CURSOR_NAME,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
    except BaseException:
        try:
            os.unlink(temp_name, dir_fd=root_fd)
        except OSError:
            pass
        raise
    os.fsync(root_fd)


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
