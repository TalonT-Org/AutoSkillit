"""Crash-safe SQLite storage for shadow context-admission accounting.

The ledger supports local filesystems whose SQLite VFS honors locking and sync
semantics. Network filesystems are intentionally outside the C2 durability
contract.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final, cast, get_args

from autoskillit.core import (
    CONTEXT_ADMISSION_ENCODING_VERSION,
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    AcceptInputEvent,
    AdmissionDecision,
    AdmissionDecisionKind,
    AdmissionSequence,
    AdmissionTransition,
    AggregateRevision,
    AuthorityUnavailableEvent,
    ContextAdmissionAccountingResult,
    ContextAdmissionAccountingStatus,
    ContextAdmissionEvent,
    ContextAdmissionInspectionResult,
    ContextAdmissionRecoveryResult,
    ContextAdmissionState,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
    ContextAdmissionStoreHealth,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    ContextLineage,
    DurableContextAdmissionPayload,
    MarkGenerationIndeterminateEvent,
    MarkIndeterminateEvent,
    OpenEpochEvent,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    RequestReconciliationEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
    ShadowContextAdmissionRecord,
    UninitializedContextAdmissionState,
    context_admission_reducer_for_protocol,
    decode_stored_context_admission_envelope,
    encode_stored_context_admission_envelope,
    make_stored_context_admission_envelope,
)

_SCHEMA_VERSION: Final = 1
_DATABASE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700
_SQLITE_PRIMARY_MASK: Final = 0xFF
_SQLITE_BUSY_CODES: Final = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
_EVENT_TYPES: Final = get_args(ContextAdmissionEvent)
_STATE_TYPES: Final = get_args(ContextAdmissionState)


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
        """Reduce and publish one event in a single immediate transaction."""
        with self._fence:
            if not self._recovered:
                self.recover_all()
            if self._store_health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                return self._storage_failure_result(stream_key)
            connection: sqlite3.Connection | None = None
            stream_id = _stream_key_bytes(stream_key)
            stream_exists = False
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT stream_key, state_envelope, aggregate_revision,
                           admission_sequence, latest_journal_sequence,
                           health_status, failure_reason, reason_code
                    FROM streams WHERE stream_id = ?
                    """,
                    (stream_id,),
                ).fetchone()
                current_state: ContextAdmissionState
                if row is None:
                    if not isinstance(event, OpenEpochEvent | AuthorityUnavailableEvent):
                        _rollback(connection)
                        return _uninitialized_stream_result(stream_key, event)
                    current_state = _zero_state(event.protocol_version)
                    genesis_envelope = _encode_value(
                        current_state,
                        protocol_version=event.protocol_version,
                    )
                    connection.execute(
                        """
                        INSERT INTO streams(
                            stream_id, stream_key, genesis_envelope, state_envelope,
                            aggregate_revision, admission_sequence,
                            latest_journal_sequence, health_status,
                            failure_reason, reason_code
                        ) VALUES (?, ?, ?, ?, 0, 0, 0, ?, NULL, NULL)
                        """,
                        (
                            stream_id,
                            stream_id,
                            genesis_envelope,
                            genesis_envelope,
                            ContextAdmissionStorageHealthStatus.HEALTHY.value,
                        ),
                    )
                    prior_revision = 0
                    prior_sequence = 0
                    prior_journal_sequence = 0
                else:
                    stream_exists = True
                    if bytes(row[0]) != stream_id:
                        raise _LedgerOpenError(
                            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                            "stream-key-mismatch",
                        )
                    if row[5] == ContextAdmissionStorageHealthStatus.FAIL_CLOSED.value:
                        _rollback(connection)
                        return ContextAdmissionAccountingResult(
                            status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
                            stream_key=stream_key,
                            failure_reason=ContextAdmissionStorageFailureReason(row[6]),
                            reason_code=str(row[7]),
                        )
                    current_state = _decode_state(bytes(row[1]))
                    prior_revision = int(row[2])
                    prior_sequence = int(row[3])
                    prior_journal_sequence = int(row[4])
                    if (
                        current_state.aggregate_revision.value != prior_revision
                        or current_state.admission_sequence.value != prior_sequence
                    ):
                        raise _LedgerOpenError(
                            ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                            "materialized-state-coordinate-mismatch",
                        )
                existing = connection.execute(
                    """
                    SELECT journal_sequence, event_envelope, decision_envelope
                    FROM journal_events
                    WHERE stream_id = ? AND event_id = ?
                    """,
                    (stream_id, event.event_id.value),
                ).fetchone()
                reducer = context_admission_reducer_for_protocol(event.protocol_version)
                if current_state.protocol_version != event.protocol_version:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
                        "stream-protocol-mismatch",
                    )
                if existing is not None:
                    original_event = _decode_event(bytes(existing[1]))
                    original_decision = _decode_decision(bytes(existing[2]))
                    if original_event != event:
                        conflict = replace(
                            original_decision,
                            kind=AdmissionDecisionKind.CONFLICT,
                            reason_code="event-id-conflict",
                        )
                        transition = AdmissionTransition(
                            next_state=current_state,
                            decision=conflict,
                            effects=(),
                        )
                        _rollback(connection)
                        return ContextAdmissionAccountingResult(
                            status=ContextAdmissionAccountingStatus.SEMANTIC_REJECTION,
                            stream_key=stream_key,
                            transition=transition,
                            reason_code=conflict.reason_code,
                        )
                    if _state_retains_event(current_state, event.event_id.value):
                        transition = reducer.reduce_transition(current_state, event)
                        if transition.effects:
                            raise _LedgerOpenError(
                                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                                "exact-replay-produced-effects",
                            )
                    else:
                        transition = AdmissionTransition(
                            next_state=current_state,
                            decision=original_decision,
                            effects=(),
                        )
                    _rollback(connection)
                    return ContextAdmissionAccountingResult(
                        status=ContextAdmissionAccountingStatus.EXACT_REPLAY,
                        stream_key=stream_key,
                        transition=transition,
                        journal_sequence=int(existing[0]),
                        reason_code=transition.decision.reason_code,
                    )
                _validate_event_stream_identity(stream_key, event)
                self._fault_callback(_LedgerFaultPoint.BEFORE_REDUCTION)
                transition = reducer.reduce_transition(current_state, event)
                self._fault_callback(_LedgerFaultPoint.AFTER_REDUCTION)
                journal_sequence = prior_journal_sequence + 1
                connection.execute(
                    """
                    INSERT INTO journal_events(
                        stream_id, journal_sequence, event_id,
                        event_envelope, decision_envelope,
                        expected_revision, prior_aggregate_revision,
                        prior_admission_sequence, resulting_aggregate_revision,
                        resulting_admission_sequence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stream_id,
                        journal_sequence,
                        event.event_id.value,
                        _encode_value(
                            event,
                            protocol_version=event.protocol_version,
                        ),
                        _encode_value(
                            transition.decision,
                            protocol_version=event.protocol_version,
                        ),
                        event.expected_aggregate_revision.value,
                        prior_revision,
                        prior_sequence,
                        transition.next_state.aggregate_revision.value,
                        transition.next_state.admission_sequence.value,
                    ),
                )
                self._fault_callback(_LedgerFaultPoint.AFTER_JOURNAL)
                self._fault_callback(_LedgerFaultPoint.DURING_EFFECTS)
                for ordinal, effect in enumerate(transition.effects):
                    connection.execute(
                        """
                        INSERT INTO effect_outbox(
                            stream_id, journal_sequence, effect_ordinal,
                            effect_envelope
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            stream_id,
                            journal_sequence,
                            ordinal,
                            _encode_value(
                                effect,
                                protocol_version=event.protocol_version,
                            ),
                        ),
                    )
                shadow = _empty_shadow_record(
                    stream_key,
                    event,
                    transition,
                    journal_sequence,
                )
                connection.execute(
                    """
                    INSERT INTO shadow_decisions(
                        stream_id, journal_sequence, shadow_envelope
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        stream_id,
                        journal_sequence,
                        _encode_value(
                            shadow,
                            protocol_version=event.protocol_version,
                        ),
                    ),
                )
                cursor = connection.execute(
                    """
                    UPDATE streams
                    SET state_envelope = ?, aggregate_revision = ?,
                        admission_sequence = ?, latest_journal_sequence = ?
                    WHERE stream_id = ? AND aggregate_revision = ?
                      AND admission_sequence = ?
                      AND latest_journal_sequence = ?
                    """,
                    (
                        _encode_value(
                            transition.next_state,
                            protocol_version=event.protocol_version,
                        ),
                        transition.next_state.aggregate_revision.value,
                        transition.next_state.admission_sequence.value,
                        journal_sequence,
                        stream_id,
                        prior_revision,
                        prior_sequence,
                        prior_journal_sequence,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                        "stream-publication-cas-failed",
                    )
                self._fault_callback(_LedgerFaultPoint.AFTER_STATE_SHADOW)
                self._fault_callback(_LedgerFaultPoint.BEFORE_COMMIT)
                self._commit(connection)
                self._fault_callback(_LedgerFaultPoint.AFTER_COMMIT)
                health = ContextAdmissionStreamHealth(
                    stream_key,
                    ContextAdmissionStorageHealthStatus.HEALTHY,
                )
                self._stream_health[stream_key] = health
                return ContextAdmissionAccountingResult(
                    status=_accounting_status(event, transition),
                    stream_key=stream_key,
                    transition=transition,
                    journal_sequence=journal_sequence,
                    reason_code=transition.decision.reason_code,
                )
            except _LedgerContended:
                if connection is not None:
                    _rollback(connection)
                return ContextAdmissionAccountingResult(
                    status=ContextAdmissionAccountingStatus.CONTENDED,
                    stream_key=stream_key,
                    reason_code="busy",
                )
            except _LedgerOpenError as exc:
                if connection is not None:
                    _rollback(connection)
                    if stream_exists and exc.reason in {
                        ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                        ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                    }:
                        self._persist_stream_failure(
                            connection,
                            stream_id,
                            stream_key,
                            exc.reason,
                            exc.reason_code,
                        )
                return ContextAdmissionAccountingResult(
                    status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
                    stream_key=stream_key,
                    failure_reason=exc.reason,
                    reason_code=exc.reason_code,
                )
            except ContextAdmissionValidationError:
                if connection is not None:
                    _rollback(connection)
                return ContextAdmissionAccountingResult(
                    status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
                    stream_key=stream_key,
                    failure_reason=ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
                    reason_code="protocol-validation-failed",
                )
            except sqlite3.Error as exc:
                if connection is not None:
                    _rollback(connection)
                if _sqlite_primary_code(exc) in _SQLITE_BUSY_CODES:
                    return ContextAdmissionAccountingResult(
                        status=ContextAdmissionAccountingStatus.CONTENDED,
                        stream_key=stream_key,
                        reason_code="busy",
                    )
                reason = (
                    ContextAdmissionStorageFailureReason.INTEGRITY
                    if _sqlite_primary_code(exc)
                    in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_CONSTRAINT}
                    else ContextAdmissionStorageFailureReason.IO
                )
                self._set_store_failure(reason, "sqlite-publication-failed")
                return self._storage_failure_result(stream_key)
            except BaseException:
                if connection is not None:
                    _rollback(connection)
                raise
            finally:
                if connection is not None:
                    connection.close()

    def reserve(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ReserveRequestEvent,
    ) -> ContextAdmissionAccountingResult:
        if not isinstance(event, ReserveRequestEvent):
            raise TypeError("reserve_requires_reserve_request_event")
        return self.apply(stream_key, event)

    def commit(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: (AcceptInputEvent | ResolveIndeterminateAcceptedEvent | ReconcileGenerationEvent),
    ) -> ContextAdmissionAccountingResult:
        if not isinstance(
            event,
            AcceptInputEvent | ResolveIndeterminateAcceptedEvent | ReconcileGenerationEvent,
        ):
            raise TypeError("commit_requires_exact_acceptance_event")
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
        if not isinstance(
            event,
            ReleaseNonAdmissionEvent
            | RollbackAdmissionEvent
            | ResolveIndeterminateNonAdmissionEvent
            | ResolveIndeterminateRollbackEvent,
        ):
            raise TypeError("release_requires_witnessed_release_event")
        return self.apply(stream_key, event)

    def _commit(self, connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + (self._busy_timeout_ms / 1_000)
        while True:
            try:
                connection.execute("COMMIT")
                return
            except sqlite3.Error as exc:
                if _sqlite_primary_code(exc) not in _SQLITE_BUSY_CODES:
                    raise
                if time.monotonic() >= deadline:
                    raise _LedgerContended from exc
                time.sleep(min(0.005, max(0.0, deadline - time.monotonic())))

    def _persist_stream_failure(
        self,
        connection: sqlite3.Connection,
        stream_id: bytes,
        stream_key: ContextAdmissionStreamKey,
        reason: ContextAdmissionStorageFailureReason,
        reason_code: str,
    ) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE streams
                SET health_status = ?, failure_reason = ?, reason_code = ?
                WHERE stream_id = ?
                """,
                (
                    ContextAdmissionStorageHealthStatus.FAIL_CLOSED.value,
                    reason.value,
                    reason_code,
                    stream_id,
                ),
            )
            connection.execute("COMMIT")
        except sqlite3.Error:
            _rollback(connection)
        self._stream_health[stream_key] = ContextAdmissionStreamHealth(
            stream_key,
            ContextAdmissionStorageHealthStatus.FAIL_CLOSED,
            failure_reason=reason,
            reason_code=reason_code,
        )

    def _storage_failure_result(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionAccountingResult:
        return ContextAdmissionAccountingResult(
            status=ContextAdmissionAccountingStatus.STORAGE_FAIL_CLOSED,
            stream_key=stream_key,
            failure_reason=(
                self._store_health.failure_reason
                or ContextAdmissionStorageFailureReason.CONFIGURATION
            ),
            reason_code=self._store_health.reason_code or "store-unavailable",
        )

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


def _zero_state(protocol_version: int) -> UninitializedContextAdmissionState:
    context_admission_reducer_for_protocol(protocol_version)
    return UninitializedContextAdmissionState(
        protocol_version=protocol_version,
        aggregate_revision=AggregateRevision(0),
        admission_sequence=AdmissionSequence(0),
        processed_events=(),
        idempotency_records=(),
        expired_idempotency_tombstones=(),
        closed_epochs=(),
    )


def _encode_value(
    value: DurableContextAdmissionPayload,
    *,
    protocol_version: int,
) -> bytes:
    return encode_stored_context_admission_envelope(
        make_stored_context_admission_envelope(
            value,
            protocol_version=protocol_version,
        )
    )


def _decode_event(value: bytes) -> ContextAdmissionEvent:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, _EVENT_TYPES):
        raise ContextAdmissionValidationError("stored_event_type_mismatch")
    return cast(ContextAdmissionEvent, payload)


def _decode_decision(value: bytes) -> AdmissionDecision:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, AdmissionDecision):
        raise ContextAdmissionValidationError("stored_decision_type_mismatch")
    return payload


def _decode_state(value: bytes) -> ContextAdmissionState:
    payload = decode_stored_context_admission_envelope(value).payload
    if not isinstance(payload, _STATE_TYPES):
        raise ContextAdmissionValidationError("stored_state_type_mismatch")
    return cast(ContextAdmissionState, payload)


def _state_retains_event(state: ContextAdmissionState, event_id: str) -> bool:
    return any(record.event_id.value == event_id for record in state.processed_events)


def _validate_event_stream_identity(
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> None:
    for lineage in _iter_lineages(event):
        if (
            lineage.root_session_id != stream_key.root_session_id
            or lineage.current_session_id != stream_key.current_session_id
            or lineage.root_agent_id != stream_key.root_agent_id
            or lineage.current_agent_id != stream_key.current_agent_id
            or lineage.root_thread_id != stream_key.root_thread_id
            or lineage.current_thread_id != stream_key.current_thread_id
            or lineage.fork_occurrence_id != stream_key.fork_occurrence_id
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                "stream-identity-mismatch",
            )


def _iter_lineages(value: object) -> tuple[ContextLineage, ...]:
    lineages: list[ContextLineage] = []

    def visit(item: object) -> None:
        if isinstance(item, ContextLineage):
            lineages.append(item)
            return
        if isinstance(item, tuple | frozenset):
            for child in item:
                visit(child)
            return
        if is_dataclass(item):
            for field_def in fields(item):
                visit(getattr(item, field_def.name))

    visit(value)
    return tuple(lineages)


def _uninitialized_stream_result(
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
) -> ContextAdmissionAccountingResult:
    decision = AdmissionDecision(
        kind=AdmissionDecisionKind.WOULD_REJECT,
        reason_code="stream-uninitialized",
        window_epoch_id=None,
        snapshot_sequence=None,
        requested_count=0,
        available_ordinary_count=0,
        available_protected_count=0,
    )
    return ContextAdmissionAccountingResult(
        status=ContextAdmissionAccountingStatus.SEMANTIC_REJECTION,
        stream_key=stream_key,
        transition=AdmissionTransition(
            next_state=_zero_state(event.protocol_version),
            decision=decision,
            effects=(),
        ),
        reason_code=decision.reason_code,
    )


def _empty_shadow_record(
    stream_key: ContextAdmissionStreamKey,
    event: ContextAdmissionEvent,
    transition: AdmissionTransition,
    journal_sequence: int,
) -> ShadowContextAdmissionRecord:
    return ShadowContextAdmissionRecord(
        stream_key=stream_key,
        event_id=event.event_id,
        journal_sequence=journal_sequence,
        aggregate_revision=transition.next_state.aggregate_revision,
        admission_sequence=transition.next_state.admission_sequence,
        decision=transition.decision,
        protocol_version=event.protocol_version,
        encoding_version=CONTEXT_ADMISSION_ENCODING_VERSION,
        reason_code=transition.decision.reason_code,
        targets=(),
    )


def _accounting_status(
    event: ContextAdmissionEvent,
    transition: AdmissionTransition,
) -> ContextAdmissionAccountingStatus:
    if transition.decision.kind is AdmissionDecisionKind.QUARANTINED:
        return ContextAdmissionAccountingStatus.PROTOCOL_QUARANTINED
    if isinstance(
        event,
        MarkIndeterminateEvent | MarkGenerationIndeterminateEvent | RequestReconciliationEvent,
    ):
        return ContextAdmissionAccountingStatus.RECONCILIATION_REQUIRED
    if transition.decision.kind in {
        AdmissionDecisionKind.WOULD_REJECT,
        AdmissionDecisionKind.WATERMARK_UNAVAILABLE,
        AdmissionDecisionKind.UPSTREAM_GATED,
        AdmissionDecisionKind.CONFLICT,
        AdmissionDecisionKind.IDEMPOTENCY_EXPIRED,
    }:
        return ContextAdmissionAccountingStatus.SEMANTIC_REJECTION
    return ContextAdmissionAccountingStatus.RECORDED


def _ignore_fault(fault_point: _LedgerFaultPoint) -> None:
    del fault_point


def _stream_key_bytes(stream_key: ContextAdmissionStreamKey) -> bytes:
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
