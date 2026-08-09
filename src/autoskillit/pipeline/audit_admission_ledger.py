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

import json
import os
import secrets
import sqlite3
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from autoskillit.core import (
    AUDIT_REFERENCE_IDENTITY_PROFILE_V1,
    ArtifactRef,
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
    AuditRound,
    AuditSlotId,
    AuditSlotKey,
    AuditVerdict,
    InstallationVersion,
    KillReason,
    RecipeExecutionId,
    ReservationDecision,
    canonical_json_bytes,
    compute_audit_reference_identity,
    compute_audit_slot_id,
    compute_bytes_hash,
    compute_canonical_hash,
)

__all__ = ["DefaultAuditAdmissionLedger"]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS installations (
    recipe_execution_id TEXT PRIMARY KEY,
    installation_version TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    retired INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS installation_occurrences (
    recipe_execution_id TEXT NOT NULL,
    installation_version TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    retired_at TEXT,
    PRIMARY KEY (recipe_execution_id, installation_version)
) STRICT;
CREATE TABLE IF NOT EXISTS slots (
    slot_id TEXT PRIMARY KEY,
    recipe_execution_id TEXT NOT NULL,
    installation_version TEXT NOT NULL,
    step_name TEXT NOT NULL,
    head_key TEXT NOT NULL,
    slot_key_json TEXT NOT NULL,
    current_attempt_id TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    slot_id TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    semantic_digest TEXT,
    correction_predecessor TEXT,
    handle_digest TEXT UNIQUE,
    reservation_json TEXT NOT NULL,
    committed_outcome_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (slot_id) REFERENCES slots(slot_id)
) STRICT;
CREATE TABLE IF NOT EXISTS prepared_effects (
    attempt_id TEXT NOT NULL,
    path TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    canonical_bytes BLOB NOT NULL,
    delivery_status TEXT NOT NULL,
    canonicalization_profile TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    PRIMARY KEY (attempt_id, path),
    FOREIGN KEY (attempt_id) REFERENCES attempts(attempt_id)
) STRICT;
CREATE TABLE IF NOT EXISTS finalization_effects (
    attempt_id TEXT NOT NULL,
    effect_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    PRIMARY KEY (attempt_id, effect_name),
    FOREIGN KEY (attempt_id) REFERENCES attempts(attempt_id)
) STRICT;
CREATE TABLE IF NOT EXISTS response_commits (
    attempt_id TEXT PRIMARY KEY,
    required_effect_names_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    replay_projection_json TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES attempts(attempt_id)
) STRICT;
CREATE TRIGGER IF NOT EXISTS reject_late_finalization_effect
BEFORE INSERT ON finalization_effects
WHEN (
    SELECT lifecycle FROM attempts WHERE attempt_id = NEW.attempt_id
) = 'RESPONSE_COMMITTED'
BEGIN
    SELECT RAISE(ABORT, 'finalization-effect-after-response-commit');
END;
CREATE TABLE IF NOT EXISTS head_claims (
    head_key TEXT PRIMARY KEY,
    recipe_execution_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    part_id TEXT NOT NULL,
    head_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS preflight_projections (
    recipe_execution_id TEXT NOT NULL,
    installation_version TEXT NOT NULL,
    step_name TEXT NOT NULL,
    plan_set_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    part_id TEXT NOT NULL,
    PRIMARY KEY (recipe_execution_id, installation_version, step_name)
) STRICT;
CREATE TABLE IF NOT EXISTS disposition_projections (
    installation_version TEXT NOT NULL,
    authority_digest TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    report_digest TEXT NOT NULL,
    report_path TEXT NOT NULL,
    association_digest TEXT NOT NULL,
    association_path TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (installation_version, authority_digest, plan_digest)
) STRICT;
"""

_METADATA_SCHEMA_VERSION = "1"
_DATABASE_MODE = 0o600
_DIRECTORY_MODE = 0o700
_HEAD_KEY_DOMAIN = "autoskillit:audit-admission:head-key:v1:sha256"
_HANDLE_DIGEST_DOMAIN = "autoskillit:audit-admission:reservation-handle:v1:sha256"
_FINALIZATION_EFFECT_READ_LIFECYCLES = frozenset(
    {
        AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION,
        AuditAttemptLifecycle.RESPONSE_COMMITTED,
    }
)
_FINALIZATION_EFFECT_ACK_LIFECYCLES = frozenset(
    {AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION}
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _head_key(
    recipe_execution_id: RecipeExecutionId,
    cycle_id: str,
    scope_id: str,
    part_id: str,
) -> str:
    return compute_canonical_hash(
        {
            "recipe_execution_id": recipe_execution_id.value,
            "cycle_id": cycle_id,
            "scope_id": scope_id,
            "part_id": part_id,
        },
        domain=_HEAD_KEY_DOMAIN,
    )


def _slot_key_to_dict(key: AuditSlotKey) -> dict[str, Any]:
    return {
        "recipe_execution_id": key.recipe_execution_id.value,
        "installation_version": key.installation_version.value,
        "step_name": key.step_name,
        "invocation_template_digest": key.invocation_template_digest,
        "slot_intent_digest": key.slot_intent_digest,
        "ordered_reference_identity": key.ordered_reference_identity,
        "prior_authority_digest": key.prior_authority_digest,
    }


def _slot_key_from_dict(data: dict[str, Any]) -> AuditSlotKey:
    return AuditSlotKey(
        recipe_execution_id=RecipeExecutionId(data["recipe_execution_id"]),
        installation_version=InstallationVersion(data["installation_version"]),
        step_name=data["step_name"],
        invocation_template_digest=data["invocation_template_digest"],
        slot_intent_digest=data["slot_intent_digest"],
        ordered_reference_identity=data["ordered_reference_identity"],
        prior_authority_digest=data["prior_authority_digest"],
    )


def _head_to_dict(head: AuditCycleHead) -> dict[str, Any]:
    return {
        "execution_generation": head.execution_generation,
        "cycle_id": head.cycle_id,
        "plan_set_id": head.plan_set_id,
        "scope_id": head.scope_id,
        "part_id": head.part_id,
        "current_authority_digest": head.current_authority_digest,
        "audit_round": head.audit_round,
        "audited_plan_refs": [ref.to_dict() for ref in head.audited_plan_refs],
        "inventory_ref": head.inventory_ref.to_dict(),
        "verdict": head.verdict.value,
        "authorized_successor_part_id": head.authorized_successor_part_id,
    }


def _head_from_dict(data: dict[str, Any]) -> AuditCycleHead:
    return AuditCycleHead(
        execution_generation=data["execution_generation"],
        cycle_id=data["cycle_id"],
        plan_set_id=data["plan_set_id"],
        scope_id=data["scope_id"],
        part_id=data["part_id"],
        current_authority_digest=data["current_authority_digest"],
        audit_round=data["audit_round"],
        audited_plan_refs=tuple(ArtifactRef.from_dict(item) for item in data["audited_plan_refs"]),
        inventory_ref=ArtifactRef.from_dict(data["inventory_ref"]),
        verdict=AuditVerdict(data["verdict"]),
        authorized_successor_part_id=data["authorized_successor_part_id"],
    )


def _reservation_to_dict(reservation: AuditIdentityReservation) -> dict[str, Any]:
    return {
        "slot_id": reservation.slot_id.value,
        "slot_key": _slot_key_to_dict(reservation.slot_key),
        "current_attempt_id": reservation.current_attempt_id.value,
        "runtime_binding_digest": reservation.runtime_binding_digest,
        "reference_identity_profile_id": reservation.reference_identity_profile_id,
        "audited_plan_refs": [ref.to_dict() for ref in reservation.audited_plan_refs],
        "plan_set_id": reservation.plan_set_id,
        "cycle_id": reservation.cycle_id,
        "scope_id": reservation.scope_id,
        "part_id": reservation.part_id,
        "audit_round": reservation.audit_round.value,
        "parent_authority_digest": reservation.parent_authority_digest,
        "generated_at": reservation.generated_at,
        "allowed_root": str(reservation.allowed_root),
        "semantic_result_path": str(reservation.semantic_result_path),
        "inventory_path": str(reservation.inventory_path),
        "authority_path": str(reservation.authority_path),
        "expected_head": (
            _head_to_dict(reservation.expected_head) if reservation.expected_head else None
        ),
        "tracker_target_order_id": reservation.tracker_target_order_id,
        "tracker_expected": reservation.tracker_expected,
    }


def _reservation_from_dict(data: dict[str, Any]) -> AuditIdentityReservation:
    expected_head = data["expected_head"]
    return AuditIdentityReservation(
        slot_id=AuditSlotId(data["slot_id"]),
        slot_key=_slot_key_from_dict(data["slot_key"]),
        current_attempt_id=AuditAttemptId(data["current_attempt_id"]),
        runtime_binding_digest=data["runtime_binding_digest"],
        reference_identity_profile_id=data["reference_identity_profile_id"],
        audited_plan_refs=tuple(ArtifactRef.from_dict(item) for item in data["audited_plan_refs"]),
        plan_set_id=data["plan_set_id"],
        cycle_id=data["cycle_id"],
        scope_id=data["scope_id"],
        part_id=data["part_id"],
        audit_round=AuditRound(data["audit_round"]),
        parent_authority_digest=data["parent_authority_digest"],
        generated_at=data["generated_at"],
        allowed_root=Path(data["allowed_root"]),
        semantic_result_path=Path(data["semantic_result_path"]),
        inventory_path=Path(data["inventory_path"]),
        authority_path=Path(data["authority_path"]),
        expected_head=(_head_from_dict(expected_head) if expected_head else None),
        tracker_target_order_id=data.get("tracker_target_order_id"),
        tracker_expected=data.get("tracker_expected", False),
    )


def _outcome_to_dict(outcome: AuditOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status.value,
        "attempt_id": outcome.attempt_id.value,
        "verdict": outcome.verdict.value if outcome.verdict is not None else None,
        "path": str(outcome.path) if outcome.path is not None else None,
        "error": outcome.error,
        "kill_reason": outcome.kill_reason.value,
        "replay_response_json": outcome.replay_response_json,
        "tracker_target_order_id": outcome.tracker_target_order_id,
        "tracker_expected": outcome.tracker_expected,
    }


def _outcome_from_dict(data: dict[str, Any]) -> AuditOutcome:
    return AuditOutcome(
        status=AuditOutcomeStatus(data["status"]),
        attempt_id=AuditAttemptId(data["attempt_id"]),
        verdict=AuditVerdict(data["verdict"]) if data["verdict"] is not None else None,
        path=Path(data["path"]) if data["path"] is not None else None,
        error=data["error"],
        kill_reason=KillReason(data.get("kill_reason", KillReason.NATURAL_EXIT.value)),
        replay_response_json=data.get("replay_response_json"),
        tracker_target_order_id=data.get("tracker_target_order_id"),
        tracker_expected=data.get("tracker_expected", False),
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

    # -- connection/schema -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        path = self._authority.database_path
        self._ensure_database_target()
        before = self._database_identity()
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=rw",
            uri=True,
            isolation_level=None,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = EXTRA")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(_SCHEMA_SQL)
            self._validate_metadata(connection)
            self._backfill_installation_occurrences(connection)
            self._validate_response_commit_integrity(connection)
            if before != self._database_identity():
                raise AuditAdmissionStorageError(
                    AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                    "audit-admission-store-identity-changed",
                )
        except Exception:
            connection.close()
            raise
        return connection

    def _ensure_database_target(self) -> None:
        path = self._authority.database_path
        parent = path.parent
        try:
            resolved_path = path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "audit-admission-insecure-store-path",
            ) from exc
        if resolved_path != path:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "audit-admission-store-path-traverses-symlink",
            )
        try:
            parent.mkdir(parents=True, mode=_DIRECTORY_MODE, exist_ok=True)
            parent_stat = parent.lstat()
        except OSError as exc:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.IO,
                "audit-admission-store-parent-unavailable",
            ) from exc
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != self._authority.expected_owner_id
            or parent_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "audit-admission-insecure-store-parent",
            )

        try:
            path.lstat()
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    path,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    _DATABASE_MODE,
                )
            except FileExistsError:
                pass
            except OSError as exc:
                raise AuditAdmissionStorageError(
                    AuditAdmissionStorageFailureReason.IO,
                    "audit-admission-store-create-failed",
                ) from exc
            else:
                os.close(descriptor)
        except OSError as exc:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.IO,
                "audit-admission-store-target-unavailable",
            ) from exc
        try:
            if path.resolve(strict=True) != path:
                raise AuditAdmissionStorageError(
                    AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                    "audit-admission-store-path-traverses-symlink",
                )
        except OSError as exc:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "audit-admission-insecure-store-file",
            ) from exc
        self._database_identity()

    def _database_identity(self) -> tuple[int, int]:
        path = self._authority.database_path
        try:
            path_stat = path.lstat()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                descriptor_stat = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "audit-admission-insecure-store-file",
            ) from exc
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_uid != self._authority.expected_owner_id
            or path_stat.st_nlink != 1
            or path_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "audit-admission-insecure-store-file",
            )
        return path_stat.st_dev, path_stat.st_ino

    def _validate_metadata(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
                (_METADATA_SCHEMA_VERSION,),
            )
            return
        if row[0] != _METADATA_SCHEMA_VERSION:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.UNSUPPORTED_SCHEMA,
                "audit-admission-schema-mismatch",
            )

    @staticmethod
    def _backfill_installation_occurrences(connection: sqlite3.Connection) -> None:
        mismatch = connection.execute(
            "SELECT 1 FROM installations AS active "
            "JOIN installation_occurrences AS occurrence "
            "ON occurrence.recipe_execution_id = active.recipe_execution_id "
            "AND occurrence.installation_version = active.installation_version "
            "WHERE occurrence.snapshot_digest != active.snapshot_digest "
            "OR occurrence.created_at != active.created_at LIMIT 1"
        ).fetchone()
        if mismatch is not None:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.INTEGRITY,
                "audit-admission-installation-history-mismatch",
            )
        connection.execute(
            "INSERT OR IGNORE INTO installation_occurrences("
            "recipe_execution_id, installation_version, snapshot_digest, created_at, retired_at"
            ") SELECT recipe_execution_id, installation_version, snapshot_digest, created_at, "
            "CASE WHEN retired = 1 THEN created_at ELSE NULL END FROM installations"
        )

    @staticmethod
    def _validate_response_commit_integrity(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT attempts.attempt_id, attempts.lifecycle, "
            "attempts.committed_outcome_json, "
            "response_commits.required_effect_names_json, "
            "response_commits.outcome_json, response_commits.replay_projection_json "
            "FROM attempts LEFT JOIN response_commits "
            "ON response_commits.attempt_id = attempts.attempt_id"
        ).fetchall()
        for (
            attempt_id,
            lifecycle_value,
            committed_outcome_json,
            required_effect_names_json,
            response_outcome_json,
            replay_projection_json,
        ) in rows:
            try:
                lifecycle = AuditAttemptLifecycle(lifecycle_value)
            except ValueError as exc:
                raise AuditAdmissionStorageError(
                    AuditAdmissionStorageFailureReason.INTEGRITY,
                    "response-commit-invalid-attempt-lifecycle",
                ) from exc
            if lifecycle is not AuditAttemptLifecycle.RESPONSE_COMMITTED:
                if required_effect_names_json is not None:
                    raise AuditAdmissionStorageError(
                        AuditAdmissionStorageFailureReason.INTEGRITY,
                        "response-commit-before-terminal-lifecycle",
                    )
                continue
            if (
                committed_outcome_json is None
                or required_effect_names_json is None
                or response_outcome_json is None
                or replay_projection_json is None
                or committed_outcome_json != response_outcome_json
            ):
                raise AuditAdmissionStorageError(
                    AuditAdmissionStorageFailureReason.INTEGRITY,
                    "response-commit-projection-mismatch",
                )
            try:
                required_effect_names = _required_effect_names_from_json(
                    required_effect_names_json
                )
                outcome = _outcome_from_dict(_json_loads(committed_outcome_json))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AuditAdmissionStorageError(
                    AuditAdmissionStorageFailureReason.INTEGRITY,
                    "response-commit-invalid-durable-projection",
                ) from exc
            if outcome.replay_response_json != replay_projection_json:
                raise AuditAdmissionStorageError(
                    AuditAdmissionStorageFailureReason.INTEGRITY,
                    "response-commit-replay-projection-mismatch",
                )
            acknowledged_effects = {
                row[0]
                for row in connection.execute(
                    "SELECT effect_name FROM finalization_effects WHERE attempt_id = ?",
                    (attempt_id,),
                )
            }
            if not set(required_effect_names).issubset(acknowledged_effects):
                raise AuditAdmissionStorageError(
                    AuditAdmissionStorageFailureReason.INTEGRITY,
                    "response-commit-finalization-effects-incomplete",
                )

    @staticmethod
    def _commit(connection: sqlite3.Connection) -> None:
        connection.execute("COMMIT")

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    # -- health/recovery -----------------------------------------------------

    def store_health(self) -> AuditAdmissionStoreHealth:
        with self._fence:
            return self._store_health

    def recover_all(self) -> AuditAdmissionRecoveryResult:
        with self._fence:
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                installations = tuple(
                    RecipeExecutionId(row[0])
                    for row in connection.execute(
                        "SELECT recipe_execution_id FROM installations WHERE retired = 0"
                    )
                )
                attempts = tuple(
                    AuditAttemptId(row[0])
                    for row in connection.execute(
                        "SELECT attempt_id FROM attempts",
                    )
                )
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
                row = connection.execute(
                    "SELECT installation_version, snapshot_digest, retired "
                    "FROM installations WHERE recipe_execution_id = ?",
                    (recipe_execution_id.value,),
                ).fetchone()
                if row is not None and not row[2]:
                    if row[1] != snapshot_digest:
                        raise AuditAdmissionStorageError(
                            AuditAdmissionStorageFailureReason.REPLAY_MISMATCH,
                            "active-installation-snapshot-mismatch",
                        )
                    version = InstallationVersion(row[0])
                    self._commit(connection)
                    return version
                version = InstallationVersion(secrets.token_hex(32))
                created_at = _now_iso()
                connection.execute(
                    "INSERT INTO installation_occurrences("
                    "recipe_execution_id, installation_version, snapshot_digest, "
                    "created_at, retired_at) VALUES (?, ?, ?, ?, NULL)",
                    (
                        recipe_execution_id.value,
                        version.value,
                        snapshot_digest,
                        created_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO installations"
                    "(recipe_execution_id, installation_version, snapshot_digest, "
                    "retired, created_at) VALUES (?, ?, ?, 0, ?) "
                    "ON CONFLICT(recipe_execution_id) DO UPDATE SET "
                    "installation_version = excluded.installation_version, "
                    "snapshot_digest = excluded.snapshot_digest, "
                    "retired = 0, created_at = excluded.created_at",
                    (
                        recipe_execution_id.value,
                        version.value,
                        snapshot_digest,
                        created_at,
                    ),
                )
                self._commit(connection)
                return version
            except BaseException:
                self._rollback(connection)
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
                connection.execute(
                    "UPDATE installations SET retired = 1 "
                    "WHERE recipe_execution_id = ? AND installation_version = ?",
                    (recipe_execution_id.value, installation_version.value),
                )
                connection.execute(
                    "UPDATE installation_occurrences SET retired_at = COALESCE(retired_at, ?) "
                    "WHERE recipe_execution_id = ? AND installation_version = ?",
                    (
                        _now_iso(),
                        recipe_execution_id.value,
                        installation_version.value,
                    ),
                )
                self._commit(connection)
            except BaseException:
                self._rollback(connection)
                raise
            finally:
                connection.close()

    def _installation_row(
        self,
        connection: sqlite3.Connection,
        recipe_execution_id: RecipeExecutionId,
    ) -> tuple[str, bool] | None:
        row = connection.execute(
            "SELECT installation_version, retired FROM installations "
            "WHERE recipe_execution_id = ?",
            (recipe_execution_id.value,),
        ).fetchone()
        if row is None:
            return None
        return row[0], bool(row[1])

    # -- reservation -------------------------------------------------------

    def reserve(self, request: AuditReservationRequest) -> AuditReservationOutcome:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                outcome = self._reserve_locked(connection, request)
                self._commit(connection)
                return outcome
            except BaseException:
                self._rollback(connection)
                raise
            finally:
                connection.close()

    def _reserve_locked(
        self,
        connection: sqlite3.Connection,
        request: AuditReservationRequest,
    ) -> AuditReservationOutcome:
        installation_row = self._installation_row(connection, request.recipe_execution_id)
        if installation_row is None or installation_row[0] != request.installation_version.value:
            raise ValueError(
                "reserve() requires a matching installation created via "
                "create_or_get_installation()"
            )
        if installation_row[1]:
            return self._conflict_outcome(request, "installation_retired")

        head_key = _head_key(
            request.recipe_execution_id,
            request.cycle_id,
            request.scope_id,
            request.part_id,
        )
        head_row = connection.execute(
            "SELECT head_json FROM head_claims WHERE head_key = ?",
            (head_key,),
        ).fetchone()
        live_head = _head_from_dict(_json_loads(head_row[0])) if head_row is not None else None
        # Slot identity is derived from the caller's EXPLICIT attested prior-authority
        # reference (request.parent_authority_digest), never from the ledger's current
        # live head: an exact redelivery of the same runtime binding must resolve to the
        # same slot even after the head has advanced past what this attempt targets.
        # Liveness against the current head is enforced later, at commit_authority()'s
        # CAS, not at slot-identity derivation time.
        current_head = (
            live_head
            if live_head is not None
            and live_head.current_authority_digest == request.parent_authority_digest
            else None
        )

        ordered_reference_identity = compute_audit_reference_identity(request.audited_plan_refs)
        slot_key = AuditSlotKey(
            recipe_execution_id=request.recipe_execution_id,
            installation_version=request.installation_version,
            step_name=request.step_name,
            invocation_template_digest=request.invocation_template_digest,
            slot_intent_digest=request.slot_intent_digest,
            ordered_reference_identity=ordered_reference_identity,
            prior_authority_digest=request.parent_authority_digest,
        )
        slot_id = compute_audit_slot_id(slot_key)

        slot_row = connection.execute(
            "SELECT current_attempt_id FROM slots WHERE slot_id = ?",
            (slot_id.value,),
        ).fetchone()

        if slot_row is None:
            if request.retry_after_audit_attempt_id is not None:
                return self._conflict_outcome(
                    request, "retry_token_unknown_slot", slot_key=slot_key
                )
            return self._dispatch_new_slot(
                connection,
                request=request,
                slot_key=slot_key,
                slot_id=slot_id,
                head_key=head_key,
                current_head=current_head,
            )

        attempt_id = AuditAttemptId(slot_row[0])
        attempt_row = connection.execute(
            "SELECT lifecycle, semantic_digest, committed_outcome_json, reservation_json "
            "FROM attempts WHERE attempt_id = ?",
            (attempt_id.value,),
        ).fetchone()
        assert attempt_row is not None
        lifecycle = AuditAttemptLifecycle(attempt_row[0])
        reservation = _reservation_from_dict(_json_loads(attempt_row[3]))

        if request.retry_after_audit_attempt_id is not None:
            if (
                lifecycle is not AuditAttemptLifecycle.SEMANTIC_REJECTED
                or request.retry_after_audit_attempt_id != attempt_id
            ):
                return self._conflict_outcome(
                    request, "retry_token_not_terminal_rejection", slot_key=slot_key
                )
            return self._dispatch_correction(
                connection,
                request=request,
                slot_id=slot_id,
                predecessor_attempt_id=attempt_id,
                reservation=reservation,
            )

        if lifecycle is AuditAttemptLifecycle.OPEN:
            return self._redispatch_open(
                connection,
                attempt_id=attempt_id,
                reservation=reservation,
            )
        if lifecycle is AuditAttemptLifecycle.PREPARED:
            return AuditReservationOutcome(
                decision=ReservationDecision.RESUME_PREPARED,
                slot_key=slot_key,
                attempt_id=attempt_id,
                reservation=reservation,
            )
        if lifecycle is AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION:
            return AuditReservationOutcome(
                decision=ReservationDecision.PUBLISHED_PENDING_FINALIZATION,
                slot_key=slot_key,
                attempt_id=attempt_id,
                reservation=reservation,
            )
        if lifecycle is AuditAttemptLifecycle.RESPONSE_COMMITTED:
            outcome = _outcome_from_dict(_json_loads(attempt_row[2]))
            return AuditReservationOutcome(
                decision=ReservationDecision.EXACT_REPLAY,
                slot_key=slot_key,
                attempt_id=attempt_id,
                replay_outcome=outcome,
            )
        if lifecycle is AuditAttemptLifecycle.SEMANTIC_REJECTED:
            return self._conflict_outcome(request, "correction_token_required", slot_key=slot_key)
        return self._conflict_outcome(
            request, f"attempt_{lifecycle.value.lower()}", slot_key=slot_key
        )

    def _conflict_outcome(
        self,
        request: AuditReservationRequest,
        detail: str,
        *,
        slot_key: AuditSlotKey | None = None,
    ) -> AuditReservationOutcome:
        resolved_slot_key = slot_key or AuditSlotKey(
            recipe_execution_id=request.recipe_execution_id,
            installation_version=request.installation_version,
            step_name=request.step_name,
            invocation_template_digest=request.invocation_template_digest,
            slot_intent_digest=request.slot_intent_digest,
            ordered_reference_identity=compute_audit_reference_identity(request.audited_plan_refs),
            prior_authority_digest=None,
        )
        return AuditReservationOutcome(
            decision=ReservationDecision.CONFLICT,
            slot_key=resolved_slot_key,
            attempt_id=AuditAttemptId(secrets.token_hex(16)),
            conflict_detail=detail,
        )

    def _build_reservation(
        self,
        *,
        request: AuditReservationRequest,
        slot_id: AuditSlotId,
        slot_key: AuditSlotKey,
        attempt_id: AuditAttemptId,
        audit_round: AuditRound,
        current_head: AuditCycleHead | None,
    ) -> AuditIdentityReservation:
        ordered_reference_identity = slot_key.ordered_reference_identity
        root = request.allowed_root / "audit-admission" / slot_id.value / attempt_id.value
        return AuditIdentityReservation(
            slot_id=slot_id,
            slot_key=slot_key,
            current_attempt_id=attempt_id,
            runtime_binding_digest=request.runtime_binding_digest,
            reference_identity_profile_id=AUDIT_REFERENCE_IDENTITY_PROFILE_V1.profile_id,
            audited_plan_refs=request.audited_plan_refs,
            plan_set_id=ordered_reference_identity,
            cycle_id=request.cycle_id,
            scope_id=request.scope_id,
            part_id=request.part_id,
            audit_round=audit_round,
            parent_authority_digest=slot_key.prior_authority_digest,
            generated_at=_now_iso(),
            allowed_root=request.allowed_root,
            semantic_result_path=root / "semantic.json",
            inventory_path=root / "inventory.json",
            authority_path=root / "authority.json",
            expected_head=current_head,
            tracker_target_order_id=request.tracker_target_order_id,
            tracker_expected=request.tracker_expected,
        )

    def _issue_handle(self, connection: sqlite3.Connection, attempt_id: AuditAttemptId) -> str:
        handle = secrets.token_hex(32)
        handle_digest = compute_bytes_hash(handle.encode("utf-8"))
        connection.execute(
            "UPDATE attempts SET handle_digest = ? WHERE attempt_id = ?",
            (f"{_HANDLE_DIGEST_DOMAIN}:{handle_digest}", attempt_id.value),
        )
        return handle

    def _dispatch_new_slot(
        self,
        connection: sqlite3.Connection,
        *,
        request: AuditReservationRequest,
        slot_key: AuditSlotKey,
        slot_id: AuditSlotId,
        head_key: str,
        current_head: AuditCycleHead | None,
    ) -> AuditReservationOutcome:
        attempt_id = AuditAttemptId(secrets.token_hex(16))
        audit_round = AuditRound(1 if current_head is None else current_head.audit_round + 1)
        reservation = self._build_reservation(
            request=request,
            slot_id=slot_id,
            slot_key=slot_key,
            attempt_id=attempt_id,
            audit_round=audit_round,
            current_head=current_head,
        )
        now = _now_iso()
        connection.execute(
            "INSERT INTO slots(slot_id, recipe_execution_id, installation_version, "
            "step_name, head_key, slot_key_json, current_attempt_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                slot_id.value,
                request.recipe_execution_id.value,
                request.installation_version.value,
                request.step_name,
                head_key,
                _json_dumps(_slot_key_to_dict(slot_key)),
                attempt_id.value,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO attempts(attempt_id, slot_id, lifecycle, semantic_digest, "
            "correction_predecessor, handle_digest, reservation_json, "
            "committed_outcome_json, created_at) "
            "VALUES (?, ?, ?, NULL, NULL, NULL, ?, NULL, ?)",
            (
                attempt_id.value,
                slot_id.value,
                AuditAttemptLifecycle.OPEN.value,
                _json_dumps(_reservation_to_dict(reservation)),
                now,
            ),
        )
        handle = self._issue_handle(connection, attempt_id)
        return AuditReservationOutcome(
            decision=ReservationDecision.DISPATCH_NEW,
            slot_key=slot_key,
            attempt_id=attempt_id,
            reservation=reservation,
            reservation_handle=handle,
        )

    def _dispatch_correction(
        self,
        connection: sqlite3.Connection,
        *,
        request: AuditReservationRequest,
        slot_id: AuditSlotId,
        predecessor_attempt_id: AuditAttemptId,
        reservation: AuditIdentityReservation,
    ) -> AuditReservationOutcome:
        attempt_id = AuditAttemptId(secrets.token_hex(16))
        next_reservation = self._build_reservation(
            request=request,
            slot_id=slot_id,
            slot_key=reservation.slot_key,
            attempt_id=attempt_id,
            audit_round=reservation.audit_round,
            current_head=reservation.expected_head,
        )
        now = _now_iso()
        connection.execute(
            "UPDATE slots SET current_attempt_id = ? WHERE slot_id = ?",
            (attempt_id.value, slot_id.value),
        )
        connection.execute(
            "INSERT INTO attempts(attempt_id, slot_id, lifecycle, semantic_digest, "
            "correction_predecessor, handle_digest, reservation_json, "
            "committed_outcome_json, created_at) "
            "VALUES (?, ?, ?, NULL, ?, NULL, ?, NULL, ?)",
            (
                attempt_id.value,
                slot_id.value,
                AuditAttemptLifecycle.OPEN.value,
                predecessor_attempt_id.value,
                _json_dumps(_reservation_to_dict(next_reservation)),
                now,
            ),
        )
        handle = self._issue_handle(connection, attempt_id)
        return AuditReservationOutcome(
            decision=ReservationDecision.DISPATCH_NEW,
            slot_key=reservation.slot_key,
            attempt_id=attempt_id,
            reservation=next_reservation,
            reservation_handle=handle,
        )

    def _redispatch_open(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_id: AuditAttemptId,
        reservation: AuditIdentityReservation,
    ) -> AuditReservationOutcome:
        handle = self._issue_handle(connection, attempt_id)
        return AuditReservationOutcome(
            decision=ReservationDecision.REDISPATCH_OPEN,
            slot_key=reservation.slot_key,
            attempt_id=attempt_id,
            reservation=reservation,
            reservation_handle=handle,
        )

    def resolve_reservation_handle(
        self,
        reservation_handle: str,
    ) -> AuditIdentityReservation | None:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                handle_digest = (
                    f"{_HANDLE_DIGEST_DOMAIN}:"
                    f"{compute_bytes_hash(reservation_handle.encode('utf-8'))}"
                )
                row = connection.execute(
                    "SELECT reservation_json, lifecycle FROM attempts WHERE handle_digest = ?",
                    (handle_digest,),
                ).fetchone()
                if row is None or row[1] != AuditAttemptLifecycle.OPEN.value:
                    return None
                return _reservation_from_dict(_json_loads(row[0]))
            finally:
                connection.close()

    # -- prepare -------------------------------------------------------------

    def prepare(self, request: AuditPrepareRequest) -> AuditPrepareOutcome:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                outcome = self._prepare_locked(connection, request)
                self._commit(connection)
                return outcome
            except BaseException:
                self._rollback(connection)
                raise
            finally:
                connection.close()

    def _prepare_locked(
        self,
        connection: sqlite3.Connection,
        request: AuditPrepareRequest,
    ) -> AuditPrepareOutcome:
        row = connection.execute(
            "SELECT a.lifecycle, a.semantic_digest, s.recipe_execution_id, "
            "s.installation_version "
            "FROM attempts a JOIN slots s ON a.slot_id = s.slot_id "
            "WHERE a.attempt_id = ?",
            (request.attempt_id.value,),
        ).fetchone()
        if row is None:
            return AuditPrepareOutcome(
                accepted=False,
                attempt_id=request.attempt_id,
                conflict_detail="unknown_attempt",
            )
        lifecycle = AuditAttemptLifecycle(row[0])
        recipe_execution_id = RecipeExecutionId(row[2])
        installation_version = row[3]
        installation_row = self._installation_row(connection, recipe_execution_id)
        if (
            installation_version != request.installation_version.value
            or installation_row is None
            or installation_row[0] != request.installation_version.value
            or installation_row[1]
        ):
            return AuditPrepareOutcome(
                accepted=False,
                attempt_id=request.attempt_id,
                conflict_detail="installation_stale",
            )
        if lifecycle is AuditAttemptLifecycle.OPEN:
            if not request.accepted:
                connection.execute(
                    "UPDATE attempts SET lifecycle = ?, semantic_digest = ? WHERE attempt_id = ?",
                    (
                        AuditAttemptLifecycle.SEMANTIC_REJECTED.value,
                        request.semantic_digest,
                        request.attempt_id.value,
                    ),
                )
                return AuditPrepareOutcome(accepted=False, attempt_id=request.attempt_id)
            connection.execute(
                "UPDATE attempts SET lifecycle = ?, semantic_digest = ? WHERE attempt_id = ?",
                (
                    AuditAttemptLifecycle.PREPARED.value,
                    request.semantic_digest,
                    request.attempt_id.value,
                ),
            )
            for effect in request.effects:
                connection.execute(
                    "INSERT INTO prepared_effects(attempt_id, path, artifact_kind, "
                    "content_digest, canonical_bytes, delivery_status, "
                    "canonicalization_profile, semantic_fingerprint) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.attempt_id.value,
                        str(effect.path),
                        effect.artifact_kind,
                        effect.content_digest,
                        effect.canonical_bytes,
                        effect.delivery_status.value,
                        effect.canonicalization_profile,
                        effect.semantic_fingerprint,
                    ),
                )
            return AuditPrepareOutcome(accepted=True, attempt_id=request.attempt_id)
        if lifecycle is AuditAttemptLifecycle.PREPARED:
            if row[1] != request.semantic_digest:
                return AuditPrepareOutcome(
                    accepted=False,
                    attempt_id=request.attempt_id,
                    conflict_detail="semantic_digest_mismatch",
                )
            existing = connection.execute(
                "SELECT path, content_digest FROM prepared_effects WHERE attempt_id = ?",
                (request.attempt_id.value,),
            ).fetchall()
            existing_by_path = {path: digest for path, digest in existing}
            for effect in request.effects:
                stored_digest = existing_by_path.get(str(effect.path))
                if stored_digest is not None and stored_digest != effect.content_digest:
                    return AuditPrepareOutcome(
                        accepted=False,
                        attempt_id=request.attempt_id,
                        conflict_detail="prepared_effect_mismatch",
                    )
            return AuditPrepareOutcome(accepted=True, attempt_id=request.attempt_id)
        return AuditPrepareOutcome(
            accepted=False,
            attempt_id=request.attempt_id,
            conflict_detail=f"attempt_{lifecycle.value.lower()}",
        )

    # -- final commit ----------------------------------------------------

    def commit_authority(self, request: AuditFinalCommitRequest) -> AuditFinalCommitOutcome:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                outcome = self._commit_authority_locked(connection, request)
                self._commit(connection)
                return outcome
            except BaseException:
                self._rollback(connection)
                raise
            finally:
                connection.close()

    def _commit_authority_locked(
        self,
        connection: sqlite3.Connection,
        request: AuditFinalCommitRequest,
    ) -> AuditFinalCommitOutcome:
        row = connection.execute(
            "SELECT a.lifecycle, s.recipe_execution_id, s.installation_version, "
            "s.head_key "
            "FROM attempts a JOIN slots s ON a.slot_id = s.slot_id "
            "WHERE a.attempt_id = ?",
            (request.attempt_id.value,),
        ).fetchone()
        if row is None:
            return AuditFinalCommitOutcome(
                committed=False,
                attempt_id=request.attempt_id,
                conflict_detail="unknown_attempt",
            )
        lifecycle, recipe_execution_id, installation_version, head_key = row
        if installation_version != request.installation_version.value:
            return AuditFinalCommitOutcome(
                committed=False,
                attempt_id=request.attempt_id,
                conflict_detail="installation_stale",
            )
        installation_row = self._installation_row(
            connection,
            RecipeExecutionId(recipe_execution_id),
        )
        if (
            installation_row is None
            or installation_row[0] != request.installation_version.value
            or installation_row[1]
        ):
            return AuditFinalCommitOutcome(
                committed=False,
                attempt_id=request.attempt_id,
                conflict_detail="installation_stale",
            )
        if lifecycle in {
            AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION.value,
            AuditAttemptLifecycle.RESPONSE_COMMITTED.value,
        }:
            return AuditFinalCommitOutcome(committed=True, attempt_id=request.attempt_id)
        if lifecycle != AuditAttemptLifecycle.PREPARED.value:
            return AuditFinalCommitOutcome(
                committed=False,
                attempt_id=request.attempt_id,
                conflict_detail=f"attempt_{lifecycle.lower()}",
            )
        head_row = connection.execute(
            "SELECT head_json FROM head_claims WHERE head_key = ?",
            (head_key,),
        ).fetchone()
        current_head = _head_from_dict(_json_loads(head_row[0])) if head_row is not None else None
        current_digest = current_head.current_authority_digest if current_head else None
        if current_digest != request.expected_head_digest:
            return AuditFinalCommitOutcome(
                committed=False,
                attempt_id=request.attempt_id,
                conflict_detail="stale_head",
            )
        if current_head is not None and current_head.verdict is AuditVerdict.GO:
            return AuditFinalCommitOutcome(
                committed=False,
                attempt_id=request.attempt_id,
                conflict_detail="terminal_head",
            )
        connection.execute(
            "INSERT INTO head_claims(head_key, recipe_execution_id, cycle_id, scope_id, "
            "part_id, head_json) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(head_key) DO UPDATE SET head_json = excluded.head_json",
            (
                head_key,
                request.new_head.execution_generation,
                request.new_head.cycle_id,
                request.new_head.scope_id,
                request.new_head.part_id,
                _json_dumps(_head_to_dict(request.new_head)),
            ),
        )
        for name in request.preflight_step_names:
            connection.execute(
                "INSERT INTO preflight_projections(recipe_execution_id, "
                "installation_version, step_name, plan_set_id, scope_id, part_id) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(recipe_execution_id, installation_version, step_name) "
                "DO UPDATE SET plan_set_id = excluded.plan_set_id, "
                "scope_id = excluded.scope_id, part_id = excluded.part_id",
                (
                    recipe_execution_id,
                    installation_version,
                    name,
                    request.new_head.plan_set_id,
                    request.new_head.scope_id,
                    (request.new_head.authorized_successor_part_id or request.new_head.part_id),
                ),
            )
        connection.execute(
            "UPDATE attempts SET lifecycle = ? WHERE attempt_id = ?",
            (AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION.value, request.attempt_id.value),
        )
        return AuditFinalCommitOutcome(committed=True, attempt_id=request.attempt_id)

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
                row = connection.execute(
                    "SELECT attempts.lifecycle, attempts.committed_outcome_json, "
                    "response_commits.required_effect_names_json, "
                    "response_commits.outcome_json, "
                    "response_commits.replay_projection_json "
                    "FROM attempts LEFT JOIN response_commits "
                    "ON response_commits.attempt_id = attempts.attempt_id "
                    "WHERE attempts.attempt_id = ?",
                    (attempt_id.value,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"finalize_response: unknown attempt {attempt_id.value}")
                lifecycle = AuditAttemptLifecycle(row[0])
                if lifecycle is AuditAttemptLifecycle.RESPONSE_COMMITTED:
                    if (
                        row[1] != outcome_json
                        or row[2] != required_effect_names_json
                        or row[3] != outcome_json
                        or row[4] != replay_projection
                    ):
                        raise AuditAdmissionStorageError(
                            AuditAdmissionStorageFailureReason.INTEGRITY,
                            "finalize-response-commit-mismatch",
                        )
                    self._commit(connection)
                    return
                if lifecycle is not AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION:
                    raise ValueError(
                        f"finalize_response: attempt {attempt_id.value} is not "
                        f"PUBLISHED_PENDING_FINALIZATION (lifecycle={lifecycle.value})"
                    )
                acknowledged_effects = {
                    effect_row[0]
                    for effect_row in connection.execute(
                        "SELECT effect_name FROM finalization_effects WHERE attempt_id = ?",
                        (attempt_id.value,),
                    )
                }
                missing_effects = set(normalized_effect_names) - acknowledged_effects
                if missing_effects:
                    raise AuditAdmissionStorageError(
                        AuditAdmissionStorageFailureReason.INTEGRITY,
                        "finalize-response-required-effects-missing",
                    )
                connection.execute(
                    "INSERT INTO response_commits("
                    "attempt_id, required_effect_names_json, outcome_json, "
                    "replay_projection_json, committed_at"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        attempt_id.value,
                        required_effect_names_json,
                        outcome_json,
                        replay_projection,
                        _now_iso(),
                    ),
                )
                connection.execute(
                    "UPDATE attempts SET lifecycle = ?, committed_outcome_json = ? "
                    "WHERE attempt_id = ?",
                    (
                        AuditAttemptLifecycle.RESPONSE_COMMITTED.value,
                        outcome_json,
                        attempt_id.value,
                    ),
                )
                self._commit(connection)
            except BaseException:
                self._rollback(connection)
                raise
            finally:
                connection.close()

    def finalization_effect_result(
        self,
        attempt_id: AuditAttemptId,
        effect_name: str,
    ) -> dict[str, Any] | None:
        self._validate_finalization_effect_name(effect_name)
        with self._fence:
            connection = self._ensure_recovered()
            try:
                self._require_finalization_effect_lifecycle(
                    connection,
                    attempt_id,
                    operation="finalization_effect_result",
                    allowed_lifecycles=_FINALIZATION_EFFECT_READ_LIFECYCLES,
                )
                row = connection.execute(
                    "SELECT result_json FROM finalization_effects "
                    "WHERE attempt_id = ? AND effect_name = ?",
                    (attempt_id.value, effect_name),
                ).fetchone()
                return None if row is None else _json_loads(row[0])
            finally:
                connection.close()

    def acknowledge_finalization_effect(
        self,
        attempt_id: AuditAttemptId,
        effect_name: str,
        result: dict[str, Any],
    ) -> None:
        self._validate_finalization_effect_name(effect_name)
        if not isinstance(result, dict) or any(not isinstance(key, str) for key in result):
            raise ValueError("finalization effect result must be a string-keyed mapping")
        result_json = _json_dumps(result)
        with self._fence:
            connection = self._ensure_recovered()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._require_finalization_effect_lifecycle(
                    connection,
                    attempt_id,
                    operation="acknowledge_finalization_effect",
                    allowed_lifecycles=_FINALIZATION_EFFECT_ACK_LIFECYCLES,
                )
                row = connection.execute(
                    "SELECT result_json FROM finalization_effects "
                    "WHERE attempt_id = ? AND effect_name = ?",
                    (attempt_id.value, effect_name),
                ).fetchone()
                if row is not None:
                    if row[0] != result_json:
                        raise AuditAdmissionStorageError(
                            AuditAdmissionStorageFailureReason.INTEGRITY,
                            "finalization-effect-result-mismatch",
                        )
                    self._commit(connection)
                    return
                connection.execute(
                    "INSERT INTO finalization_effects("
                    "attempt_id, effect_name, result_json, acknowledged_at"
                    ") VALUES (?, ?, ?, ?)",
                    (attempt_id.value, effect_name, result_json, _now_iso()),
                )
                self._commit(connection)
            except BaseException:
                self._rollback(connection)
                raise
            finally:
                connection.close()

    @staticmethod
    def _validate_finalization_effect_name(effect_name: str) -> None:
        if not isinstance(effect_name, str) or not effect_name.strip():
            raise ValueError("finalization effect name must be a non-empty string")

    @staticmethod
    def _require_finalization_effect_lifecycle(
        connection: sqlite3.Connection,
        attempt_id: AuditAttemptId,
        *,
        operation: str,
        allowed_lifecycles: frozenset[AuditAttemptLifecycle],
    ) -> AuditAttemptLifecycle:
        row = connection.execute(
            "SELECT lifecycle FROM attempts WHERE attempt_id = ?",
            (attempt_id.value,),
        ).fetchone()
        if row is None:
            raise ValueError(f"{operation}: unknown attempt {attempt_id.value}")
        lifecycle = AuditAttemptLifecycle(row[0])
        if lifecycle not in allowed_lifecycles:
            raise ValueError(
                f"{operation}: attempt {attempt_id.value} is not eligible for "
                f"finalization (lifecycle={lifecycle.value})"
            )
        return lifecycle

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
                head_key = _head_key(recipe_execution_id, cycle_id, scope_id, part_id)
                row = connection.execute(
                    "SELECT head_json FROM head_claims WHERE head_key = ?",
                    (head_key,),
                ).fetchone()
                return _head_from_dict(_json_loads(row[0])) if row is not None else None
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
                row = connection.execute(
                    "SELECT plan_set_id, scope_id, part_id FROM preflight_projections "
                    "WHERE recipe_execution_id = ? AND installation_version = ? "
                    "AND step_name = ?",
                    (recipe_execution_id.value, installation_version.value, step_name),
                ).fetchone()
                if row is None:
                    return None
                return AuditPreflightProjection(
                    plan_set_id=row[0], scope_id=row[1], part_id=row[2]
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
                outcome = self._commit_disposition_locked(connection, request)
                self._commit(connection)
                return outcome
            except BaseException:
                self._rollback(connection)
                raise
            finally:
                connection.close()

    def _commit_disposition_locked(
        self,
        connection: sqlite3.Connection,
        request: AuditDispositionCommitRequest,
    ) -> AuditDispositionCommitOutcome:
        existing = connection.execute(
            "SELECT report_digest, report_path, association_digest, association_path, "
            "generated_at FROM disposition_projections "
            "WHERE installation_version = ? AND authority_digest = ? AND plan_digest = ?",
            (request.installation_version.value, request.authority_digest, request.plan_digest),
        ).fetchone()
        if existing is not None:
            if existing[:4] != (
                request.report_digest,
                str(request.report_path),
                request.association_digest,
                str(request.association_path),
            ):
                return AuditDispositionCommitOutcome(
                    committed=False,
                    generated_at=existing[4],
                    conflict_detail="disposition_projection_mismatch",
                )
            return AuditDispositionCommitOutcome(committed=True, generated_at=existing[4])
        installation = self._installation_row(connection, request.recipe_execution_id)
        if (
            installation is None
            or installation[0] != request.installation_version.value
            or installation[1]
        ):
            return AuditDispositionCommitOutcome(
                committed=False,
                generated_at=request.generated_at,
                conflict_detail="installation_stale",
            )
        head_key = _head_key(
            request.recipe_execution_id,
            request.cycle_id,
            request.scope_id,
            request.part_id,
        )
        head_row = connection.execute(
            "SELECT head_json FROM head_claims WHERE head_key = ?",
            (head_key,),
        ).fetchone()
        current_head = _head_from_dict(_json_loads(head_row[0])) if head_row is not None else None
        if (
            current_head is None
            or current_head.current_authority_digest != request.authority_digest
        ):
            return AuditDispositionCommitOutcome(
                committed=False,
                generated_at=request.generated_at,
                conflict_detail="stale_authority",
            )
        connection.execute(
            "INSERT INTO disposition_projections(installation_version, authority_digest, "
            "plan_digest, report_digest, report_path, association_digest, "
            "association_path, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.installation_version.value,
                request.authority_digest,
                request.plan_digest,
                request.report_digest,
                str(request.report_path),
                request.association_digest,
                str(request.association_path),
                request.generated_at,
            ),
        )
        return AuditDispositionCommitOutcome(committed=True, generated_at=request.generated_at)

    def resolve_disposition(
        self,
        *,
        authority_digest: str,
        plan_digest: str,
    ) -> Path | None:
        with self._fence:
            connection = self._ensure_recovered()
            try:
                row = connection.execute(
                    "SELECT report_path FROM disposition_projections "
                    "WHERE authority_digest = ? AND plan_digest = ? "
                    "ORDER BY generated_at DESC LIMIT 1",
                    (authority_digest, plan_digest),
                ).fetchone()
                return Path(row[0]) if row is not None else None
            finally:
                connection.close()


def _normalize_required_effect_names(
    required_effect_names: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(required_effect_names, tuple) or not required_effect_names:
        raise ValueError("required_effect_names must be a non-empty tuple")
    normalized: list[str] = []
    for effect_name in required_effect_names:
        if (
            not isinstance(effect_name, str)
            or not effect_name
            or effect_name != effect_name.strip()
        ):
            raise ValueError("required_effect_names must contain normalized non-empty strings")
        normalized.append(effect_name)
    if len(set(normalized)) != len(normalized):
        raise ValueError("required_effect_names cannot contain duplicates")
    return tuple(sorted(normalized))


def _required_effect_names_to_json(required_effect_names: tuple[str, ...]) -> str:
    return _json_dumps({"effect_names": list(required_effect_names)})


def _required_effect_names_from_json(payload_json: str) -> tuple[str, ...]:
    payload = _json_loads(payload_json)
    if set(payload) != {"effect_names"} or not isinstance(payload["effect_names"], list):
        raise ValueError("invalid durable required_effect_names projection")
    return _normalize_required_effect_names(tuple(payload["effect_names"]))


def _validate_replay_projection(outcome: AuditOutcome) -> str:
    replay_projection = outcome.replay_response_json
    if replay_projection is None:
        raise ValueError("finalize_response requires replay_response_json")
    try:
        projection = json.loads(replay_projection)
    except json.JSONDecodeError as exc:
        raise ValueError("replay_response_json must be a JSON object") from exc
    if not isinstance(projection, dict):
        raise ValueError("replay_response_json must be a JSON object")
    return replay_projection


def _json_dumps(payload: dict[str, Any]) -> str:
    return canonical_json_bytes(payload).decode("utf-8")


def _json_loads(text: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(text)
    return result
