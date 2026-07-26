"""Crash-safe SQLite storage for shadow context-admission accounting.

The ledger supports local filesystems whose SQLite VFS honors locking and sync
semantics. Network filesystems are intentionally outside the C2 durability
contract.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import stat
import threading
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Final

from autoskillit.core import (
    CONTEXT_ADMISSION_ENCODING_VERSION,
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    AcceptInputEvent,
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionInspectionResult,
    ContextAdmissionRecoveryResult,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
    ContextAdmissionStoreHealth,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
)

_SCHEMA_VERSION: Final = 1
_DATABASE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700
_SQLITE_PRIMARY_MASK: Final = 0xFF
_SQLITE_BUSY_CODES: Final = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})


class _LedgerFaultPoint(StrEnum):
    BEFORE_REDUCTION = "before_reduction"
    AFTER_REDUCTION = "after_reduction"
    AFTER_JOURNAL = "after_journal"
    DURING_EFFECTS = "during_effects"
    AFTER_STATE_SHADOW = "after_state_shadow"
    BEFORE_COMMIT = "before_commit"
    AFTER_COMMIT = "after_commit"


class _LedgerOpenError(RuntimeError):
    def __init__(
        self,
        reason: ContextAdmissionStorageFailureReason,
        reason_code: str,
    ) -> None:
        super().__init__(reason_code)
        self.reason = reason
        self.reason_code = reason_code


class _LedgerContended(RuntimeError):
    pass


class DefaultContextAdmissionLedger:
    """SQLite-backed context-admission journal and verified projections."""

    def __init__(
        self,
        authority: ContextAdmissionStoreAuthority,
        *,
        busy_timeout_ms: int = 50,
        fault_callback: Callable[[_LedgerFaultPoint], None] | None = None,
        connection_factory: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or not 0 <= busy_timeout_ms <= 5_000
        ):
            raise ValueError("invalid_context_admission_busy_timeout")
        self._authority = authority
        self._path = authority.database_path
        self._busy_timeout_ms = busy_timeout_ms
        self._fault_callback = fault_callback or _ignore_fault
        self._connection_factory = connection_factory
        self._fence = threading.RLock()
        self._recovered = False
        self._store_health = ContextAdmissionStoreHealth(
            ContextAdmissionStorageHealthStatus.UNINITIALIZED
        )
        self._stream_health: dict[
            ContextAdmissionStreamKey,
            ContextAdmissionStreamHealth,
        ] = {}

    @property
    def database_path(self) -> Path:
        """Return the configured path for diagnostics that already hold authority."""
        return self._path

    def store_health(self) -> ContextAdmissionStoreHealth:
        with self._fence:
            return self._store_health

    def stream_health(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionStreamHealth:
        with self._fence:
            return self._stream_health.get(
                stream_key,
                ContextAdmissionStreamHealth(
                    stream_key,
                    ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                ),
            )

    def apply(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ContextAdmissionEvent,
    ) -> ContextAdmissionAccountingResult:
        """Apply is enabled after atomic publication is installed in the next phase."""
        del event
        return ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
            stream_key=stream_key,
            failure_reason=ContextAdmissionStorageFailureReason.CONFIGURATION,
            reason_code="publication-unavailable",
        )

    def reserve(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ReserveRequestEvent,
    ) -> ContextAdmissionAccountingResult:
        return self.apply(stream_key, event)

    def commit(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: (AcceptInputEvent | ResolveIndeterminateAcceptedEvent | ReconcileGenerationEvent),
    ) -> ContextAdmissionAccountingResult:
        return self.apply(stream_key, event)

    def release(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: (
            ReleaseNonAdmissionEvent
            | RollbackAdmissionEvent
            | ResolveIndeterminateNonAdmissionEvent
            | ResolveIndeterminateRollbackEvent
        ),
    ) -> ContextAdmissionAccountingResult:
        return self.apply(stream_key, event)

    def recover(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionRecoveryResult:
        result = self.recover_all()
        if result.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
            return result
        with self._fence:
            health = self.stream_health(stream_key)
            stream_healths = (
                (health,)
                if health.status is not ContextAdmissionStorageHealthStatus.UNINITIALIZED
                else ()
            )
            return ContextAdmissionRecoveryResult(
                status=result.status,
                store_health=result.store_health,
                stream_healths=stream_healths,
                recovered_streams=(
                    (stream_key,)
                    if health.status is ContextAdmissionStorageHealthStatus.HEALTHY
                    else ()
                ),
                unresolved_streams=(),
            )

    def recover_all(self) -> ContextAdmissionRecoveryResult:
        with self._fence:
            if self._store_health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                return self._recovery_result()
            connection: sqlite3.Connection | None = None
            try:
                self._ensure_store()
                connection = self._connect()
                self._validate_integrity(connection)
                metadata = dict(connection.execute("SELECT key, value FROM metadata"))
                self._validate_metadata(metadata)
                stream_count = int(
                    connection.execute("SELECT COUNT(*) FROM streams").fetchone()[0]
                )
                if stream_count:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.AMBIGUOUS_RECOVERY,
                        "stream-replay-pending",
                    )
                self._store_health = ContextAdmissionStoreHealth(
                    ContextAdmissionStorageHealthStatus.HEALTHY
                )
                self._recovered = True
            except _LedgerContended:
                return ContextAdmissionRecoveryResult(
                    status=ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                    store_health=self._store_health,
                    stream_healths=(),
                    recovered_streams=(),
                    unresolved_streams=(),
                )
            except _LedgerOpenError as exc:
                self._set_store_failure(exc.reason, exc.reason_code)
            except sqlite3.Error as exc:
                reason = (
                    ContextAdmissionStorageFailureReason.INTEGRITY
                    if _sqlite_primary_code(exc) == sqlite3.SQLITE_CORRUPT
                    else ContextAdmissionStorageFailureReason.IO
                )
                self._set_store_failure(reason, "sqlite-recovery-failed")
            finally:
                if connection is not None:
                    connection.close()
            return self._recovery_result()

    def replay(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult:
        return self.inspect_stream(stream_key)

    def inspect_stream(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult:
        health = self.stream_health(stream_key)
        return ContextAdmissionInspectionResult(
            stream_key=stream_key,
            health=health,
            state=None,
            events=(),
            decisions=(),
            effects=(),
            shadows=(),
            latest_journal_sequence=0,
        )

    def _recovery_result(self) -> ContextAdmissionRecoveryResult:
        healths = tuple(
            sorted(
                self._stream_health.values(),
                key=lambda item: _stream_key_bytes(item.stream_key),
            )
        )
        return ContextAdmissionRecoveryResult(
            status=self._store_health.status,
            store_health=self._store_health,
            stream_healths=healths,
            recovered_streams=tuple(
                health.stream_key
                for health in healths
                if health.status is ContextAdmissionStorageHealthStatus.HEALTHY
            ),
            unresolved_streams=(),
        )

    def _ensure_store(self) -> None:
        self._ensure_private_parent()
        if self._path.exists():
            self._validate_database_file()
            self._validate_sidecars(allow_regular=True)
            return
        self._validate_sidecars(allow_regular=False)
        temporary_path = self._path.parent / (f".{self._path.name}.{secrets.token_hex(12)}.tmp")
        try:
            descriptor = os.open(
                temporary_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _DATABASE_MODE,
            )
            os.close(descriptor)
            connection = self._configure_connection(temporary_path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA_SQL.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                metadata = {
                    "schema_version": str(_SCHEMA_VERSION),
                    "encoding_version": str(CONTEXT_ADMISSION_ENCODING_VERSION),
                    "protocol_version": str(CONTEXT_ADMISSION_PROTOCOL_VERSION),
                    "store_health": ContextAdmissionStorageHealthStatus.HEALTHY.value,
                }
                connection.executemany(
                    "INSERT INTO metadata(key, value) VALUES(?, ?)",
                    tuple(metadata.items()),
                )
                connection.execute("COMMIT")
            except BaseException:
                _rollback(connection)
                raise
            finally:
                connection.close()
            os.chmod(temporary_path, _DATABASE_MODE)
            _fsync_file(temporary_path)
            os.link(temporary_path, self._path, follow_symlinks=False)
            os.unlink(temporary_path)
            _fsync_directory(self._path.parent)
            self._validate_database_file()
        except FileExistsError as exc:
            if self._path.exists():
                self._validate_database_file()
                return
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "store-publication-collision",
            ) from exc
        except _LedgerOpenError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IO,
                "store-initialization-failed",
            ) from exc
        finally:
            _unlink_initialization_artifact(temporary_path)

    def _ensure_private_parent(self) -> None:
        parent = self._path.parent
        trusted_parent = parent.parent
        try:
            trusted_stat = trusted_parent.lstat()
            if (
                not stat.S_ISDIR(trusted_stat.st_mode)
                or stat.S_ISLNK(trusted_stat.st_mode)
                or trusted_stat.st_uid != self._authority.expected_owner_id
            ):
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                    "untrusted-store-parent",
                )
            try:
                parent.mkdir(mode=_DIRECTORY_MODE)
                _fsync_directory(trusted_parent)
            except FileExistsError:
                pass
            parent_stat = parent.lstat()
        except _LedgerOpenError:
            raise
        except OSError as exc:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IO,
                "store-parent-unavailable",
            ) from exc
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or stat.S_ISLNK(parent_stat.st_mode)
            or parent_stat.st_uid != self._authority.expected_owner_id
            or stat.S_IMODE(parent_stat.st_mode) != _DIRECTORY_MODE
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "insecure-store-parent",
            )

    def _validate_database_file(self) -> tuple[int, int]:
        return _private_file_identity(
            self._path,
            owner_id=self._authority.expected_owner_id,
            reason_code="insecure-store-file",
        )

    def _validate_sidecars(self, *, allow_regular: bool) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(f"{self._path}{suffix}")
            try:
                sidecar.lstat()
            except FileNotFoundError:
                continue
            if not allow_regular:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                    "orphan-store-sidecar",
                )
            _private_file_identity(
                sidecar,
                owner_id=self._authority.expected_owner_id,
                reason_code="insecure-store-sidecar",
            )

    def _connect(self) -> sqlite3.Connection:
        before = self._validate_database_file()
        self._validate_sidecars(allow_regular=True)
        connection = self._configure_connection(self._path)
        try:
            after = self._validate_database_file()
            if before != after:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                    "store-identity-changed",
                )
            return connection
        except BaseException:
            connection.close()
            raise

    def _configure_connection(self, path: Path) -> sqlite3.Connection:
        try:
            connection = self._connection_factory(
                f"file:{path}?mode=rw",
                uri=True,
                timeout=self._busy_timeout_ms / 1_000,
                isolation_level=None,
            )
            expected_pragmas = (
                ("journal_mode", "DELETE", ("delete",)),
                ("synchronous", "EXTRA", (3,)),
                ("foreign_keys", "ON", (1,)),
                ("busy_timeout", str(self._busy_timeout_ms), (self._busy_timeout_ms,)),
            )
            for name, value, expected in expected_pragmas:
                connection.execute(f"PRAGMA {name}={value}")
                row = connection.execute(f"PRAGMA {name}").fetchone()
                if row != expected:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.CONFIGURATION,
                        "sqlite-pragma-mismatch",
                    )
            return connection
        except _LedgerOpenError:
            raise
        except sqlite3.Error as exc:
            if _sqlite_primary_code(exc) in _SQLITE_BUSY_CODES:
                raise _LedgerContended from exc
            raise

    @staticmethod
    def _validate_integrity(connection: sqlite3.Connection) -> None:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        if row != ("ok",):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.INTEGRITY,
                "sqlite-integrity-failed",
            )

    @staticmethod
    def _validate_metadata(metadata: dict[str, str]) -> None:
        expected = {
            "schema_version": str(_SCHEMA_VERSION),
            "encoding_version": str(CONTEXT_ADMISSION_ENCODING_VERSION),
            "protocol_version": str(CONTEXT_ADMISSION_PROTOCOL_VERSION),
            "store_health": ContextAdmissionStorageHealthStatus.HEALTHY.value,
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                reason = {
                    "schema_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_SCHEMA,
                    "encoding_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
                    "protocol_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
                }.get(key, ContextAdmissionStorageFailureReason.INTEGRITY)
                raise _LedgerOpenError(reason, f"invalid-{key.replace('_', '-')}")

    def _set_store_failure(
        self,
        reason: ContextAdmissionStorageFailureReason,
        reason_code: str,
    ) -> None:
        self._store_health = ContextAdmissionStoreHealth(
            ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
            failure_reason=reason,
            reason_code=reason_code,
        )
        self._recovered = True


def _ignore_fault(fault_point: _LedgerFaultPoint) -> None:
    del fault_point


def _stream_key_bytes(stream_key: ContextAdmissionStreamKey) -> bytes:
    import json

    return json.dumps(
        stream_key.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _private_file_identity(
    path: Path,
    *,
    owner_id: int,
    reason_code: str,
) -> tuple[int, int]:
    try:
        path_stat = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            descriptor_stat = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
            reason_code,
        ) from exc
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or stat.S_ISLNK(path_stat.st_mode)
        or path_stat.st_uid != owner_id
        or stat.S_IMODE(path_stat.st_mode) != _DATABASE_MODE
        or path_stat.st_nlink != 1
        or (path_stat.st_dev, path_stat.st_ino) != (descriptor_stat.st_dev, descriptor_stat.st_ino)
    ):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
            reason_code,
        )
    return path_stat.st_dev, path_stat.st_ino


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_initialization_artifact(path: Path) -> None:
    for candidate in (path, Path(f"{path}-journal")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _rollback(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _sqlite_primary_code(error: sqlite3.Error) -> int | None:
    code = getattr(error, "sqlite_errorcode", None)
    return code & _SQLITE_PRIMARY_MASK if isinstance(code, int) else None


_SCHEMA_SQL = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE streams (
    stream_id BLOB PRIMARY KEY,
    stream_key BLOB NOT NULL UNIQUE,
    genesis_envelope BLOB NOT NULL,
    state_envelope BLOB NOT NULL,
    aggregate_revision INTEGER NOT NULL,
    admission_sequence INTEGER NOT NULL,
    latest_journal_sequence INTEGER NOT NULL,
    health_status TEXT NOT NULL,
    failure_reason TEXT,
    reason_code TEXT
) STRICT;
CREATE TABLE journal_events (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    event_envelope BLOB NOT NULL,
    decision_envelope BLOB NOT NULL,
    expected_revision INTEGER NOT NULL,
    prior_aggregate_revision INTEGER NOT NULL,
    prior_admission_sequence INTEGER NOT NULL,
    resulting_aggregate_revision INTEGER NOT NULL,
    resulting_admission_sequence INTEGER NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence),
    UNIQUE (stream_id, event_id),
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id)
) STRICT;
CREATE TABLE effect_outbox (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    effect_ordinal INTEGER NOT NULL,
    effect_envelope BLOB NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence, effect_ordinal),
    FOREIGN KEY (stream_id, journal_sequence)
        REFERENCES journal_events(stream_id, journal_sequence)
) STRICT;
CREATE TABLE shadow_decisions (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    shadow_envelope BLOB NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence),
    FOREIGN KEY (stream_id, journal_sequence)
        REFERENCES journal_events(stream_id, journal_sequence)
) STRICT;
"""

__all__ = ["DefaultContextAdmissionLedger"]
