"""Crash-safe SQLite storage for shadow context-admission accounting.

The implementation is decomposed into cohesive shards:

* :mod:`._codec` — protocol-v1 envelope codec and stream-key serialization
* :mod:`._projection` — replay-projection and stored-health decoding
* :mod:`._shadow` — shadow projection registry and target-constructors
* :mod:`._state_queries` — pure state/event identity predicates
* :mod:`._store` — sidecar/parent/file-init, connection configuration, and
  persisted-state validators
* :mod:`._status` — fault points and accounting-status constructors
* :mod:`._sqlite_errors` — busy/recovery code masks, ``_LedgerContended``,
  rollback helper, ``_sqlite_primary_code`` classifier
* :mod:`._apply` — apply transaction boundary and busy-retry commit
* :mod:`._recover` — apply-time recovery orchestration (mid-flight rollback
  + re-recovery + re-apply)
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
from ._projection import (  # noqa: F401  (rebound by `_recover`)
    _recover_stream_projection,
    _stored_stream_health,
)
from ._sqlite_errors import (  # noqa: E402, F401
    _SQLITE_BUSY_CODES,
    _LedgerContended,
    _rollback,
    _sqlite_primary_code,
)
from ._state_queries import _state_has_unresolved_work  # noqa: F401  (rebound by `_recover`)
from ._status import _ignore_fault, _LedgerFaultPoint, _set_store_failure

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

# __all__ lists the stable public surface. The two import blocks above are
# cross-shard glue used by the rebind block and `recover_all` body below; those
# symbols are internal and not re-exported.
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
        if not callable(connection_factory):
            raise TypeError("invalid_context_admission_connection_factory")
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
    # Stubs declared for mypy Protocol conformance; rebound from sibling shards
    # at module bottom before any instance is constructed.

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
        result = self.recover_all()  # type: ignore[attr-defined]
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
# Methods live in sibling shards; bound onto the class below (Wavefront 1 of #4667).

from ._apply import (  # noqa: E402
    _commit_with_busy_retry as _commit_with_busy_retry_method,
)
from ._apply import (  # noqa: E402
    _persist_stream_failure as _persist_stream_failure_method,
)
from ._apply import (  # noqa: E402
    _storage_failure_result as _storage_failure_result_method,
)
from ._apply import (  # noqa: E402
    apply as _apply_method,
)
from ._apply import (  # noqa: E402
    commit as _commit_method,
)
from ._apply import (  # noqa: E402
    release as _release_method,
)
from ._apply import (  # noqa: E402
    reserve as _reserve_method,
)

setattr(DefaultContextAdmissionLedger, "apply", _apply_method)
setattr(DefaultContextAdmissionLedger, "reserve", _reserve_method)
setattr(DefaultContextAdmissionLedger, "commit", _commit_method)
setattr(DefaultContextAdmissionLedger, "release", _release_method)
setattr(
    DefaultContextAdmissionLedger,
    "_commit_with_busy_retry",
    _commit_with_busy_retry_method,
)
setattr(DefaultContextAdmissionLedger, "_persist_stream_failure", _persist_stream_failure_method)
setattr(DefaultContextAdmissionLedger, "_storage_failure_result", _storage_failure_result_method)

setattr(DefaultContextAdmissionLedger, "_set_store_failure", _set_store_failure)

from ._store import (  # noqa: E402
    _configure_connection,
    _connect,
    _ensure_private_parent,
    _ensure_store,
    _has_initialization_link,
    _recover_initialization_link,
    _validate_database_file,
    _validate_integrity,
    _validate_metadata,
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

from ._inspection import _inspect_stream as _inspect_stream_method  # noqa: E402

setattr(DefaultContextAdmissionLedger, "inspect_stream", _inspect_stream_method)

from ._recover import (  # noqa: E402
    _recover_sqlite_result as _recover_sqlite_result_method,
)
from ._recover import recover_all as recover_all_method  # noqa: E402

setattr(DefaultContextAdmissionLedger, "recover_all", recover_all_method)

setattr(
    DefaultContextAdmissionLedger,
    "_recover_sqlite_result",
    _recover_sqlite_result_method,
)


# ── Recovery rebinds ───────────────────────────────────────────────────────
# `recover_all` lives in `_recover` and references the `_MAX_RECOVERY_BYTES`
# / `_MAX_RECOVERY_ROWS` rebinds at module top; the `_INT` suffix is no longer
# needed here since the consumer is in a different shard.
