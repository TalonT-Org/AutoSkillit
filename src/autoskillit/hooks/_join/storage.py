"""Atomic storage for the fixed-set join ledger."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

from .declaration import _canonical

JOIN_LEDGER_SCHEMA_VERSION = 2


class _CorruptedLedger(Exception):
    """Raised when the on-disk ledger cannot be parsed safely."""


def _empty_payload() -> dict[str, Any]:
    return {
        "schema_version": JOIN_LEDGER_SCHEMA_VERSION,
        "sessions": {},
        "batches": {},
        "declaration_index": {},
    }


def _read_locked(ledger_path: Path) -> dict[str, Any]:
    try:
        raw = ledger_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_payload()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _CorruptedLedger(f"join ledger is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _CorruptedLedger("join ledger top level must be an object")
    if payload.get("schema_version") != JOIN_LEDGER_SCHEMA_VERSION:
        raise _CorruptedLedger(
            "unsupported join ledger schema_version: "
            f"{payload.get('schema_version')!r}; expected {JOIN_LEDGER_SCHEMA_VERSION}"
        )
    fields = ("sessions", "batches", "declaration_index")
    if not all(isinstance(payload.get(field), dict) for field in fields):
        raise _CorruptedLedger("join ledger v2 indexes must be objects")
    return payload


def write_join_ledger(ledger_path: Path, payload: dict[str, Any]) -> None:
    """Persist one locked ledger snapshot through atomic replacement."""
    encoded = _canonical(payload).encode("utf-8")
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".join_ledger.", suffix=".tmp", dir=str(ledger_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, ledger_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


LEDGER_FILENAME = "join_ledger.json"

LOCK_FILENAME = "join_ledger.lock"

_LOCK_ACQUIRE_TIMEOUT_SECONDS = 2.0

_LOCK_RETRY_INTERVAL_SECONDS = 0.01


def ledger_paths(flag_dir: Path) -> tuple[Path, Path]:
    return (flag_dir / LEDGER_FILENAME, flag_dir / LOCK_FILENAME)


def _acquire_lock(fd: int) -> None:
    """Acquire an exclusive lock without waiting indefinitely."""
    deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OSError(
                    errno.EWOULDBLOCK,
                    "timed out acquiring the join-ledger lock",
                ) from None
            time.sleep(min(_LOCK_RETRY_INTERVAL_SECONDS, remaining))
        else:
            return


@contextlib.contextmanager
def _flock(lock_path: Path) -> Generator[int, None, None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    locked = False
    try:
        _acquire_lock(fd)
        locked = True
        yield fd
    finally:
        try:
            if locked:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)
