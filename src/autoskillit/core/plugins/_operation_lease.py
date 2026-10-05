"""Deadline-bounded evidence for an in-flight MCP tool call.

Operation leases are not locks and do not prove that a child's work is making
progress. A live producer renews the lease file's mtime; readers accept it only
while that mtime is fresh and its recorded deadline has not passed. This assumes
the producer and supervisor share a filesystem with coherent timestamps. The
supervisor is responsible for supplying a channel on such a filesystem.
"""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import Token
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import anyio

from ..io.io import read_versioned_json, write_versioned_json
from ..logging import get_logger
from ..types.protocols._type_protocols_execution import InFlightOperationsProtocol

__all__ = [
    "InFlightOperations",
    "OPERATION_LEASE_FRESHNESS_SECONDS",
    "OPERATION_LEASE_HEARTBEAT_SECONDS",
    "OperationLeaseHandle",
    "OperationLeaseRecord",
    "current_operation_lease",
    "operation_lease",
    "read_active_operation_leases",
]

logger = get_logger(__name__)

OPERATION_LEASE_HEARTBEAT_SECONDS: Final = 30.0
OPERATION_LEASE_FRESHNESS_SECONDS: Final = 90.0
_LEASE_SUFFIX: Final = ".lease.json"
_SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class OperationLeaseRecord:
    operation_id: str
    operation: str
    started_at_epoch: float
    not_after_epoch: float

    def __post_init__(self) -> None:
        for name, value in (
            ("started_at_epoch", self.started_at_epoch),
            ("not_after_epoch", self.not_after_epoch),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite epoch timestamp")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be a finite epoch timestamp")
        if self.not_after_epoch <= self.started_at_epoch:
            raise ValueError("not_after_epoch must be later than started_at_epoch")

    def to_payload(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "operation": self.operation,
            "started_at_epoch": self.started_at_epoch,
            "not_after_epoch": self.not_after_epoch,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> OperationLeaseRecord:
        schema_version = payload.get("schema_version")
        if isinstance(schema_version, bool) or schema_version != _SCHEMA_VERSION:
            raise ValueError("operation lease schema version is unsupported")
        operation_id = payload.get("operation_id")
        operation = payload.get("operation")
        started_at = payload.get("started_at_epoch")
        not_after = payload.get("not_after_epoch")
        if not isinstance(operation_id, str) or not isinstance(operation, str):
            raise ValueError("operation lease identity fields must be strings")
        if (
            isinstance(started_at, bool)
            or not isinstance(started_at, (int, float))
            or isinstance(not_after, bool)
            or not isinstance(not_after, (int, float))
        ):
            raise ValueError("operation lease epochs must be numbers")
        return cls(operation_id, operation, float(started_at), float(not_after))


class InFlightOperations:
    """Thread-safe count of tool operations currently inside the kitchen."""

    __slots__ = ("_active_count", "_lock")

    def __init__(self) -> None:
        self._active_count = 0
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    def _enter(self) -> None:
        with self._lock:
            self._active_count += 1

    def _exit(self) -> None:
        with self._lock:
            self._active_count -= 1


class OperationLeaseHandle:
    """Mutable deadline and current channel path for one in-flight operation."""

    __slots__ = ("record", "path")

    def __init__(self, record: OperationLeaseRecord, path: Path | None) -> None:
        self.record = record
        self.path = path

    def _retire_file(self) -> None:
        path = self.path
        self.path = None
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("operation_lease_unlink_failed", path=str(path), exc_info=True)

    def narrow(self, not_after_epoch: float) -> None:
        if isinstance(not_after_epoch, bool) or not isinstance(not_after_epoch, (int, float)):
            raise ValueError("not_after_epoch must be a finite epoch timestamp")
        if not math.isfinite(not_after_epoch):
            raise ValueError("not_after_epoch must be a finite epoch timestamp")
        if not_after_epoch >= self.record.not_after_epoch:
            return
        if not_after_epoch <= self.record.started_at_epoch:
            self._retire_file()
            raise ValueError("operation lease deadline has already expired")

        self.record = replace(self.record, not_after_epoch=float(not_after_epoch))
        path = self.path
        if path is None:
            return
        if not path.exists():
            self.path = None
            return
        try:
            write_versioned_json(path, self.record.to_payload(), schema_version=_SCHEMA_VERSION)
        except OSError:
            self.path = None
            logger.warning("operation_lease_narrow_write_failed", path=str(path), exc_info=True)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("operation_lease_unlink_failed", path=str(path), exc_info=True)
            raise


def current_operation_lease() -> OperationLeaseHandle | None:
    from ..pipeline._step_context import _CURRENT_LEASE  # circular-break

    return _CURRENT_LEASE.get()


async def _heartbeat(handle: OperationLeaseHandle, interval: float) -> None:
    while True:
        await anyio.sleep(interval)
        path = handle.path
        if path is None:
            return
        try:
            os.utime(path, None)
        except FileNotFoundError:
            if handle.path == path:
                handle.path = None
            return


@asynccontextmanager
async def operation_lease(
    channel_dir: Path | None,
    *,
    operation: str,
    not_after_epoch: float,
    registry: InFlightOperationsProtocol,
    heartbeat_interval: float = OPERATION_LEASE_HEARTBEAT_SECONDS,
) -> AsyncGenerator[OperationLeaseHandle, None]:
    """Register, publish, and clean up one deadline-bounded tool operation."""
    record = OperationLeaseRecord(
        operation_id=uuid.uuid4().hex,
        operation=operation,
        started_at_epoch=time.time(),
        not_after_epoch=not_after_epoch,
    )
    from ..pipeline._step_context import _CURRENT_LEASE  # circular-break

    handle = OperationLeaseHandle(record, None)
    registry._enter()
    heartbeat_task: asyncio.Task[None] | None = None
    token: Token[OperationLeaseHandle | None] | None = None
    try:
        if channel_dir is not None:
            path = channel_dir / f"{record.operation_id}{_LEASE_SUFFIX}"
            try:
                write_versioned_json(path, record.to_payload(), schema_version=_SCHEMA_VERSION)
            except OSError:
                logger.warning("operation_lease_write_failed", path=str(path), exc_info=True)
            else:
                handle.path = path
                heartbeat_task = asyncio.get_running_loop().create_task(
                    _heartbeat(handle, heartbeat_interval)
                )
        token = _CURRENT_LEASE.set(handle)
        yield handle
    finally:
        try:
            if token is not None:
                _CURRENT_LEASE.reset(token)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
            try:
                handle._retire_file()
            finally:
                registry._exit()
            if heartbeat_task is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.warning("operation_lease_heartbeat_failed", exc_info=True)


def read_active_operation_leases(
    channel_dir: Path,
    *,
    now_epoch: float,
    freshness_seconds: float = OPERATION_LEASE_FRESHNESS_SECONDS,
) -> tuple[OperationLeaseRecord, ...]:
    """Return valid, fresh leases whose deadlines have not passed."""
    records: list[OperationLeaseRecord] = []
    try:
        paths = tuple(channel_dir.glob(f"*{_LEASE_SUFFIX}"))
    except OSError:
        logger.debug("operation_lease_channel_unreadable", path=str(channel_dir), exc_info=True)
        return ()
    for path in paths:
        try:
            stat_result = path.stat()
        except OSError:
            continue
        if now_epoch - stat_result.st_mtime > freshness_seconds:
            continue
        payload = read_versioned_json(path, _SCHEMA_VERSION)
        if payload is None:
            logger.debug("operation_lease_read_skipped", path=str(path))
            continue
        try:
            record = OperationLeaseRecord.from_payload(payload)
        except (TypeError, ValueError):
            logger.debug("operation_lease_read_skipped", path=str(path), exc_info=True)
            continue
        if now_epoch >= record.not_after_epoch:
            continue
        records.append(record)
    records.sort(key=lambda record: record.not_after_epoch)
    return tuple(records)
