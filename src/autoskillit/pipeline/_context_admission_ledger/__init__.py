"""Crash-safe SQLite storage for shadow context-admission accounting.

The implementation is decomposed into cohesive shards:

* :mod:`._codec` — protocol-v1 envelope codec and stream-key serialization
* :mod:`._projection` — replay-projection and stored-health decoding
* :mod:`._shadow` — shadow projection registry and target-constructors
* :mod:`._state_queries` — pure state/event identity predicates
* :mod:`._store` — sidecar/parent/file-init and connection configuration
* :mod:`._status` — busy/recovery code masks, fault points, accounting status
* :mod:`._apply` — apply transaction boundary and busy-retry commit
* :mod:`._inspection` — inspection helpers and the ``inspect_stream`` body
* :mod:`._storage` — filesystem and bounded SQLite primitives (originally
  ``_context_admission_storage.py``)

This module is the public entry point: it declares
:class:`DefaultContextAdmissionLedger` and binds the per-shard methods onto
the class. See Wavefront 1 of #4667.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any  # noqa: F401  (retained for forward compatibility with type stubs)

from autoskillit.core import (
    AcceptInputEvent,
    ContextAdmissionAccountingResult,
    ContextAdmissionEvent,
    ContextAdmissionInspectionResult,
    ContextAdmissionRecoveryResult,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
    ContextAdmissionStoreAuthority,
    ContextAdmissionStoreHealth,
    ContextAdmissionStreamHealth,
    ContextAdmissionStreamKey,
    ContextAdmissionValidationError,
    ReconcileGenerationEvent,
    ReleaseNonAdmissionEvent,
    ReserveRequestEvent,
    ResolveIndeterminateAcceptedEvent,
    ResolveIndeterminateNonAdmissionEvent,
    ResolveIndeterminateRollbackEvent,
    RollbackAdmissionEvent,
)

# Internal cross-shard re-exports used by the class body below.
from ._codec import (  # noqa: F401  (used by `recover_all`, `_recovery_result`)
    _decode_stream_key,
    _stream_key_bytes,
)
from ._projection import (  # noqa: F401  (used by `recover_all`)
    _recover_stream_projection,
    _stored_stream_health,
)
from ._state_queries import _state_has_unresolved_work  # noqa: F401  (used by `recover_all`)
from ._status import _ignore_fault, _LedgerFaultPoint, _set_store_failure  # noqa: F401

# Re-exports for cross-package consumers that import from the public surface.
from ._storage import (
    SCHEMA_SQL,
    _LedgerOpenError,
    _LedgerReadBudget,
    _preflight_storage_routes,
    _read_bounded_rows,
    fsync_directory,  # noqa: F401  (re-exported via package import path)
    fsync_file,  # noqa: F401  (re-exported via package import path)
    reconcile_initialization_links,  # noqa: F401  (re-exported via package import path)
    require_private_file_identity,  # noqa: F401  (re-exported via package import path)
    unlink_initialization_artifact,  # noqa: F401  (re-exported via package import path)
)
from ._storage import (
    validate_sidecars as _validate_sidecars,  # noqa: F401  (rebound at module load)
)

__all__ = [
    "DefaultContextAdmissionLedger",
    "SCHEMA_SQL",
    "_LedgerOpenError",
]


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
        self._unresolved_streams: set[ContextAdmissionStreamKey] = set()

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

    # ── Protocol-required method declarations ───────────────────────────────
    # These stubs declare the public surface required by
    # :class:`autoskillit.core.types._type_context_admission_persistence.ContextAdmissionLedger`
    # at class-definition time so mypy can verify structural conformance.
    # The actual implementations are rebound onto the class from sibling
    # shards at module bottom (Wavefront 1 of #4667). At runtime, these
    # stubs are overwritten before any instance is constructed; the Protocol
    # check sees the rebound implementations.

    def apply(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ContextAdmissionEvent,
    ) -> ContextAdmissionAccountingResult:
        raise NotImplementedError  # rebound from ._apply at module load

    def reserve(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: ReserveRequestEvent,
    ) -> ContextAdmissionAccountingResult:
        raise NotImplementedError  # rebound from ._apply at module load

    def commit(
        self,
        stream_key: ContextAdmissionStreamKey,
        event: AcceptInputEvent | ResolveIndeterminateAcceptedEvent | ReconcileGenerationEvent,
    ) -> ContextAdmissionAccountingResult:
        raise NotImplementedError  # rebound from ._apply at module load

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
        raise NotImplementedError  # bound from ._apply at module load

    def inspect_stream(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult:
        raise NotImplementedError  # rebound from ._inspection at module load

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
                unresolved_streams=(
                    (stream_key,) if stream_key in self._unresolved_streams else ()
                ),
            )

    def recover_all(self) -> ContextAdmissionRecoveryResult:
        with self._fence:
            if self._recovered:
                return self._recovery_result()
            connection: sqlite3.Connection | None = None
            pending_stream_failures: list[
                tuple[
                    bytes,
                    ContextAdmissionStreamKey,
                    ContextAdmissionStorageFailureReason,
                    str,
                ]
            ] = []
            try:
                self._ensure_store()  # type: ignore[attr-defined]
                connection = self._connect()  # type: ignore[attr-defined]
                connection.execute("BEGIN")
                connection.setlimit(
                    sqlite3.SQLITE_LIMIT_LENGTH,
                    max(1, _MAX_RECOVERY_BYTES_INT),
                )
                read_budget = _LedgerReadBudget(
                    "recovery-read-limit-exceeded",
                    max_rows=_MAX_RECOVERY_ROWS_INT,
                    max_bytes=_MAX_RECOVERY_BYTES_INT,
                )
                self._validate_integrity(connection)  # type: ignore[attr-defined]
                metadata = dict(
                    _read_bounded_rows(
                        connection.execute("SELECT key, value FROM metadata"),
                        read_budget,
                    )
                )
                self._validate_metadata(metadata)  # type: ignore[attr-defined]
                _preflight_storage_routes(connection, read_budget)
                self._stream_health.clear()
                self._unresolved_streams.clear()
                stream_rows = _read_bounded_rows(
                    connection.execute(
                        """
                        SELECT stream_id, stream_key, genesis_envelope, state_envelope,
                               aggregate_revision, admission_sequence,
                               latest_journal_sequence, health_status,
                               failure_reason, reason_code
                        FROM streams
                        ORDER BY stream_id
                        """
                    ),
                    read_budget,
                )

                for row in stream_rows:
                    stream_id = bytes(row[0])
                    stream_key = _decode_stream_key(bytes(row[1]))
                    if stream_id != bytes(row[1]) or stream_id != _stream_key_bytes(stream_key):
                        raise _LedgerOpenError(
                            ContextAdmissionStorageFailureReason.IDENTITY_MISMATCH,
                            "stream-key-mismatch",
                        )
                    health = _stored_stream_health(stream_key, row[7], row[8], row[9])
                    if health.status is ContextAdmissionStorageHealthStatus.FAIL_CLOSED:
                        self._stream_health[stream_key] = health
                        continue
                    if health.status is not ContextAdmissionStorageHealthStatus.HEALTHY:
                        pending_stream_failures.append(
                            (
                                stream_id,
                                stream_key,
                                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                                "invalid-stream-health",
                            )
                        )
                        continue
                    try:
                        recovered_state = _recover_stream_projection(
                            connection,
                            stream_id,
                            stream_key,
                            genesis_envelope=bytes(row[2]),
                            materialized_state_envelope=bytes(row[3]),
                            aggregate_revision=int(row[4]),
                            admission_sequence=int(row[5]),
                            latest_journal_sequence=int(row[6]),
                            read_budget=read_budget,
                        )[0]
                    except ContextAdmissionValidationError:
                        pending_stream_failures.append(
                            (
                                stream_id,
                                stream_key,
                                ContextAdmissionStorageFailureReason.REPLAY_MISMATCH,
                                "stream-replay-decode-failed",
                            )
                        )
                        continue
                    except _LedgerOpenError as exc:
                        pending_stream_failures.append(
                            (
                                stream_id,
                                stream_key,
                                exc.reason,
                                exc.reason_code,
                            )
                        )
                        continue
                    self._stream_health[stream_key] = ContextAdmissionStreamHealth(
                        stream_key,
                        ContextAdmissionStorageHealthStatus.HEALTHY,
                    )
                    if _state_has_unresolved_work(recovered_state):
                        self._unresolved_streams.add(stream_key)
                connection.execute("COMMIT")
                for stream_id, stream_key, reason, reason_code in pending_stream_failures:
                    persisted = self._persist_stream_failure(  # type: ignore[attr-defined]
                        connection,
                        stream_id,
                        stream_key,
                        reason,
                        reason_code,
                    )
                    if not persisted:
                        if (
                            self._store_health.status
                            is not ContextAdmissionStorageHealthStatus.FAIL_CLOSED
                        ):
                            raise _LedgerContended_import
                        break
                if (
                    self._store_health.status
                    is not ContextAdmissionStorageHealthStatus.FAIL_CLOSED
                ):
                    self._store_health = ContextAdmissionStoreHealth(
                        ContextAdmissionStorageHealthStatus.HEALTHY
                    )
                self._recovered = True
            except _LedgerContended_import:
                self._stream_health.clear()
                self._unresolved_streams.clear()
                return ContextAdmissionRecoveryResult(
                    status=ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                    store_health=self._store_health,
                    stream_healths=(),
                    recovered_streams=(),
                    unresolved_streams=(),
                )
            except _LedgerOpenError as exc:
                self._set_store_failure(exc.reason, exc.reason_code)  # type: ignore[attr-defined]
            except sqlite3.Error as exc:
                primary_code = _sqlite_primary_code_import(exc)
                if primary_code in _SQLITE_BUSY_CODES_import:
                    if connection is not None:
                        _rollback_import(connection)
                    self._stream_health.clear()
                    self._unresolved_streams.clear()
                    return ContextAdmissionRecoveryResult(
                        status=ContextAdmissionStorageHealthStatus.UNINITIALIZED,
                        store_health=self._store_health,
                        stream_healths=(),
                        recovered_streams=(),
                        unresolved_streams=(),
                    )
                if primary_code == sqlite3.SQLITE_TOOBIG:
                    self._set_store_failure(  # type: ignore[attr-defined]
                        ContextAdmissionStorageFailureReason.INTEGRITY,
                        "recovery-read-limit-exceeded",
                    )
                    return self._recovery_result()
                reason = (
                    ContextAdmissionStorageFailureReason.INTEGRITY
                    if primary_code == sqlite3.SQLITE_CORRUPT
                    else ContextAdmissionStorageFailureReason.IO
                )
                self._set_store_failure(reason, "sqlite-recovery-failed")  # type: ignore[attr-defined]
            finally:
                if connection is not None:
                    connection.close()
            return self._recovery_result()

    def replay(
        self,
        stream_key: ContextAdmissionStreamKey,
    ) -> ContextAdmissionInspectionResult:
        return self.inspect_stream(stream_key)  # type: ignore[attr-defined]

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
            unresolved_streams=tuple(sorted(self._unresolved_streams, key=_stream_key_bytes)),
        )


# ── Method rebind block ─────────────────────────────────────────────────────
# The class declaration above is intentionally lean. Transaction-boundary,
# store-side, status-side, and inspection methods live in sibling shards
# and are bound onto the class below. This is the ONLY place methods are
# rebound (Wavefront 1 of #4667 — Foundation Finding 1 + Interface Finding 3).

from ._apply import (  # noqa: E402
    _commit as _commit_method,
)
from ._apply import (  # noqa: E402
    _persist_stream_failure as _persist_stream_failure_method,
)
from ._apply import (  # noqa: E402
    _recover_sqlite_result as _recover_sqlite_result_method,
)
from ._apply import (  # noqa: E402
    _storage_failure_result as _storage_failure_result_method,
)
from ._apply import (  # noqa: E402
    apply as _apply_method,
)
from ._apply import (  # noqa: E402
    commit as _commit_public_method,
)
from ._apply import (  # noqa: E402
    release as _release_method,
)
from ._apply import (  # noqa: E402
    reserve as _reserve_method,
)

setattr(DefaultContextAdmissionLedger, "apply", _apply_method)
setattr(DefaultContextAdmissionLedger, "reserve", _reserve_method)
setattr(DefaultContextAdmissionLedger, "commit", _commit_public_method)
setattr(DefaultContextAdmissionLedger, "release", _release_method)
setattr(DefaultContextAdmissionLedger, "_commit", _commit_method)
setattr(DefaultContextAdmissionLedger, "_persist_stream_failure", _persist_stream_failure_method)
setattr(DefaultContextAdmissionLedger, "_recover_sqlite_result", _recover_sqlite_result_method)
setattr(DefaultContextAdmissionLedger, "_storage_failure_result", _storage_failure_result_method)

from ._status import (  # noqa: E402
    _validate_integrity,
    _validate_metadata,
)

setattr(DefaultContextAdmissionLedger, "_set_store_failure", _set_store_failure)
setattr(
    DefaultContextAdmissionLedger,
    "_validate_integrity",
    staticmethod(_validate_integrity),
)
setattr(
    DefaultContextAdmissionLedger,
    "_validate_metadata",
    staticmethod(_validate_metadata),
)

from ._store import (  # noqa: E402
    _configure_connection,
    _connect,
    _ensure_private_parent,
    _ensure_store,
    _has_initialization_link,
    _recover_initialization_link,
    _validate_database_file,
)

setattr(DefaultContextAdmissionLedger, "_ensure_store", _ensure_store)
setattr(
    DefaultContextAdmissionLedger, "_recover_initialization_link", _recover_initialization_link
)
setattr(DefaultContextAdmissionLedger, "_has_initialization_link", _has_initialization_link)
setattr(DefaultContextAdmissionLedger, "_ensure_private_parent", _ensure_private_parent)
setattr(DefaultContextAdmissionLedger, "_validate_database_file", _validate_database_file)
setattr(DefaultContextAdmissionLedger, "_connect", _connect)
setattr(DefaultContextAdmissionLedger, "_configure_connection", _configure_connection)

from ._inspection import _inspect_stream as _inspect_stream_method  # noqa: E402

setattr(DefaultContextAdmissionLedger, "inspect_stream", _inspect_stream_method)


# ── Local re-bind of cross-shard constants used by `recover_all` body ─────
from ._projection import (  # noqa: E402
    _MAX_RECOVERY_BYTES as _MAX_RECOVERY_BYTES_INT,
)
from ._projection import (  # noqa: E402
    _MAX_RECOVERY_ROWS as _MAX_RECOVERY_ROWS_INT,
)
from ._status import (  # noqa: E402
    _SQLITE_BUSY_CODES as _SQLITE_BUSY_CODES_import,
)
from ._status import (  # noqa: E402
    _LedgerContended as _LedgerContended_import,
)
from ._status import (  # noqa: E402
    _rollback as _rollback_import,
)
from ._status import (  # noqa: E402
    _sqlite_primary_code as _sqlite_primary_code_import,
)
