"""Crash-safe SQLite storage for the parent-owned audit admission ledger.

Follows the context-admission persistence pattern (`context_admission_ledger.py`):
every logical transition opens its own connection, takes a ``BEGIN IMMEDIATE``
write lock, rereads the current installation/slot/attempt/head state, and
atomically writes the new trusted state for that transition in the same
transaction. A per-instance ``threading.RLock`` additionally serializes
same-process callers so the reread-then-write sequence never races itself.

The ledger is the sole mutable authority for audit installations, slots,
attempts, heads, preflight projections, and committed dispositions.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, ClassVar, Literal

from autoskillit.core import (
    AuditAdmissionAuthorityMismatchError,
    AuditAdmissionRecoveryResult,
    AuditAdmissionStorageError,
    AuditAdmissionStorageFailureReason,
    AuditAdmissionStorageHealthStatus,
    AuditAdmissionStoreAuthority,
    AuditAdmissionStoreHealth,
    AuditAttemptId,
    AuditAttemptLifecycle,
    AuditCycleHead,
    AuditDispositionCommitOutcome,
    AuditDispositionCommitRequest,
    AuditFinalCommitOutcome,
    AuditFinalCommitRequest,
    AuditIdentityReservation,
    AuditOutcome,
    AuditOutcomeStatus,
    AuditPreflightProjection,
    AuditPrepareOutcome,
    AuditPrepareRequest,
    AuditReservationOutcome,
    AuditReservationRequest,
    InstallationVersion,
    RecipeExecutionId,
    compute_bytes_hash,
)
from autoskillit.pipeline._audit_admission_ledger import (
    _authority,
    _connections,
    _disposition,
    _finalization,
    _installations,
    _prepare,
    _reads,
    _recovery,
    _reservations,
)
from autoskillit.pipeline._audit_admission_ledger._encoders import (
    _HANDLE_DIGEST_DOMAIN,
    _HANDLE_PREFIX,
    _json_dumps,
    _normalize_required_effect_names,
    _now_iso,
    _outcome_to_dict,
    _required_effect_names_to_json,
    _validate_replay_projection,
)

__all__ = ["DefaultAuditAdmissionLedger", "_now_iso"]

_FINALIZATION_EFFECT_READ_LIFECYCLES = frozenset(
    {
        AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION,
        AuditAttemptLifecycle.RESPONSE_COMMITTED,
    }
)
_FINALIZATION_EFFECT_ACK_LIFECYCLES = frozenset(
    {AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION}
)


class DefaultAuditAdmissionLedger:
    """SQLite-backed `AuditAdmissionLedger` implementation."""

    # This versioned policy intentionally exposes no compaction transition:
    # occurrence, attempt, acknowledgement, and response records remain durable
    # for arbitrarily late valid replay and conflict detection.
    retention_policy_id: ClassVar[Literal["audit-admission-retention:indefinite:v1"]] = (
        "audit-admission-retention:indefinite:v1"
    )

    def __init__(
        self,
        authority: AuditAdmissionStoreAuthority,
        *,
        busy_timeout_ms: int = 2000,
    ) -> None:
        self._authority = authority
        self._busy_timeout_ms = busy_timeout_ms
        self._fence = threading.RLock()
        self._recovered = False
        self._store_health = AuditAdmissionStoreHealth(
            status=AuditAdmissionStorageHealthStatus.UNRECOVERED,
        )

    @property
    def store_authority(self) -> AuditAdmissionStoreAuthority:
        return self._authority

    # -- health/recovery -----------------------------------------------------

    def store_health(self) -> AuditAdmissionStoreHealth:
        with self._fence:
            return self._store_health

    def recover_all(self) -> AuditAdmissionRecoveryResult:
        with self._fence:
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                installations, attempts = _recovery._installations_and_attempts_read(connection)
            except AuditAdmissionStorageError as exc:
                return self._fail_closed_recovery(exc.reason, exc.reason_code)
            except (OSError, sqlite3.Error) as exc:
                return self._fail_closed_recovery(
                    AuditAdmissionStorageFailureReason.IO,
                    f"audit-admission-recovery-failed:{type(exc).__name__}",
                )
            finally:
                if connection is not None:
                    connection.close()
            self._store_health = AuditAdmissionStoreHealth(
                status=AuditAdmissionStorageHealthStatus.HEALTHY,
            )
            self._recovered = True
            return AuditAdmissionRecoveryResult(
                store_health=self._store_health,
                recovered_installations=installations,
                recovered_attempts=attempts,
            )

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection to the authority's database.

        Retained as an instance method so existing tests that monkeypatch
        ``ledger._connect`` to inject a recovery fault continue to work
        after the connection primitive moved into ``_connections``.
        """
        return _connections.open(self._authority, self._busy_timeout_ms)

    def _fail_closed_recovery(
        self,
        reason: AuditAdmissionStorageFailureReason,
        reason_code: str,
    ) -> AuditAdmissionRecoveryResult:
        self._store_health = AuditAdmissionStoreHealth(
            status=AuditAdmissionStorageHealthStatus.FAIL_CLOSED,
            failure_reason=reason,
            reason_code=reason_code,
        )
        self._recovered = False
        return AuditAdmissionRecoveryResult(
            store_health=self._store_health,
            recovered_installations=(),
            recovered_attempts=(),
        )

    def _ensure_recovered(self) -> sqlite3.Connection:
        if not self._recovered:
            self.recover_all()
        if self._store_health.status is not AuditAdmissionStorageHealthStatus.HEALTHY:
            raise AuditAdmissionStorageError(
                self._store_health.failure_reason or AuditAdmissionStorageFailureReason.IO,
                self._store_health.reason_code or "audit-admission-unrecovered",
            )
        return self._connect()

    # -- installations ---------------------------------------------------

    def create_or_get_installation(
        self,
        *,
        recipe_execution_id: RecipeExecutionId,
        snapshot_digest: str,
    ) -> InstallationVersion:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                version = _installations._create_or_get_installation_locked(
                    connection,
                    recipe_execution_id=recipe_execution_id,
                    snapshot_digest=snapshot_digest,
                )
                _connections.commit(connection)
                return version
            except BaseException:
                _connections.rollback(connection)
                raise
            finally:
                connection.close()

    def retire_installation(
        self,
        *,
        recipe_execution_id: RecipeExecutionId,
        installation_version: InstallationVersion,
    ) -> None:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                _installations._retire_installation_locked(
                    connection,
                    recipe_execution_id=recipe_execution_id,
                    installation_version=installation_version,
                )
                _connections.commit(connection)
            except BaseException:
                _connections.rollback(connection)
                raise
            finally:
                connection.close()

    # -- reservation -------------------------------------------------------

    def reserve(self, request: AuditReservationRequest) -> AuditReservationOutcome:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                outcome = _reservations._reserve_locked(
                    connection, request, authority_id=self._authority.authority_id
                )
                _connections.commit(connection)
                return outcome
            except BaseException:
                _connections.rollback(connection)
                raise
            finally:
                connection.close()

    def resolve_reservation_handle(
        self,
        reservation_handle: str,
    ) -> AuditIdentityReservation | None:
        parts = reservation_handle.split(".")
        if len(parts) != 3 or parts[0] != _HANDLE_PREFIX:
            return None
        handle_authority_id, secret = parts[1:]
        if (
            not AuditAdmissionStoreAuthority.is_valid_authority_id(handle_authority_id)
            or len(secret) != 64
            or any(char not in "0123456789abcdef" for char in secret)
        ):
            return None
        serving_authority_id = self._authority.authority_id
        if handle_authority_id != serving_authority_id:
            raise AuditAdmissionAuthorityMismatchError(
                handle_authority_id,
                serving_authority_id,
            )
        with self._fence:
            connection = self._ensure_recovered()
            try:
                handle_digest = (
                    f"{_HANDLE_DIGEST_DOMAIN}:{compute_bytes_hash(secret.encode('utf-8'))}"
                )
                return _reservations._resolve_reservation_handle_read(
                    connection, handle_digest=handle_digest
                )
            finally:
                connection.close()

    # -- prepare -------------------------------------------------------------

    def prepare(self, request: AuditPrepareRequest) -> AuditPrepareOutcome:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                outcome = _prepare._prepare_locked(connection, request)
                _connections.commit(connection)
                return outcome
            except BaseException:
                _connections.rollback(connection)
                raise
            finally:
                connection.close()

    # -- final commit ----------------------------------------------------

    def commit_authority(self, request: AuditFinalCommitRequest) -> AuditFinalCommitOutcome:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                outcome = _authority._commit_authority_locked(connection, request)
                _connections.commit(connection)
                return outcome
            except BaseException:
                _connections.rollback(connection)
                raise
            finally:
                connection.close()

    def finalize_response(
        self,
        attempt_id: AuditAttemptId,
        outcome: AuditOutcome,
        *,
        required_effect_names: tuple[str, ...],
    ) -> None:
        if outcome.attempt_id != attempt_id:
            raise ValueError("finalize_response outcome.attempt_id does not match attempt_id")
        if outcome.status is not AuditOutcomeStatus.PUBLISHED:
            raise ValueError("finalize_response requires a PUBLISHED outcome")
        normalized_effect_names = _normalize_required_effect_names(required_effect_names)
        replay_projection = _validate_replay_projection(outcome)
        required_effect_names_json = _required_effect_names_to_json(normalized_effect_names)
        outcome_json = _json_dumps(_outcome_to_dict(outcome))
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                _finalization._finalize_response_locked(
                    connection,
                    attempt_id,
                    outcome_json=outcome_json,
                    required_effect_names_json=required_effect_names_json,
                    replay_projection=replay_projection,
                    normalized_effect_names=normalized_effect_names,
                )
                _connections.commit(connection)
            except BaseException:
                _connections.rollback(connection)
                raise
            finally:
                connection.close()

    def finalization_effect_result(
        self,
        attempt_id: AuditAttemptId,
        effect_name: str,
    ) -> dict[str, Any] | None:
        _finalization._validate_finalization_effect_name(effect_name)
        with self._fence:
            connection = self._ensure_recovered()
            try:
                return _finalization._finalization_effect_result_read(
                    connection,
                    attempt_id,
                    effect_name,
                    allowed_lifecycles=_FINALIZATION_EFFECT_READ_LIFECYCLES,
                )
            finally:
                connection.close()

    def acknowledge_finalization_effect(
        self,
        attempt_id: AuditAttemptId,
        effect_name: str,
        result: dict[str, Any],
    ) -> None:
        _finalization._validate_finalization_effect_name(effect_name)
        if not isinstance(result, dict) or any(not isinstance(key, str) for key in result):
            raise ValueError("finalization effect result must be a string-keyed mapping")
        result_json = _json_dumps(result)
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                _finalization._acknowledge_finalization_effect_locked(
                    connection,
                    attempt_id,
                    effect_name,
                    result_json,
                    allowed_lifecycles=_FINALIZATION_EFFECT_ACK_LIFECYCLES,
                )
                _connections.commit(connection)
            except BaseException:
                _connections.rollback(connection)
                raise
            finally:
                connection.close()

    # -- reads -------------------------------------------------------------

    def current_head(
        self,
        *,
        recipe_execution_id: RecipeExecutionId,
        cycle_id: str,
        scope_id: str,
        part_id: str,
    ) -> AuditCycleHead | None:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                return _reads._current_head_read(
                    connection,
                    recipe_execution_id=recipe_execution_id,
                    cycle_id=cycle_id,
                    scope_id=scope_id,
                    part_id=part_id,
                )
            finally:
                connection.close()

    def preflight_projection(
        self,
        *,
        recipe_execution_id: RecipeExecutionId,
        installation_version: InstallationVersion,
        step_name: str,
    ) -> AuditPreflightProjection | None:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                return _reads._preflight_projection_read(
                    connection,
                    recipe_execution_id=recipe_execution_id,
                    installation_version=installation_version,
                    step_name=step_name,
                )
            finally:
                connection.close()

    # -- disposition -------------------------------------------------------

    def commit_disposition(
        self,
        request: AuditDispositionCommitRequest,
    ) -> AuditDispositionCommitOutcome:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                outcome = _disposition._commit_disposition_locked(connection, request)
                _connections.commit(connection)
                return outcome
            except BaseException:
                _connections.rollback(connection)
                raise
            finally:
                connection.close()

    def resolve_disposition(
        self,
        *,
        authority_digest: str,
        plan_digest: str,
    ) -> Path | None:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                return _disposition._resolve_disposition_read(
                    connection,
                    authority_digest=authority_digest,
                    plan_digest=plan_digest,
                )
            finally:
                connection.close()
