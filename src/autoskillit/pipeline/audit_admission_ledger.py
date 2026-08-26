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
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from autoskillit.core import (
    ArtifactRef,
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
    AuditRound,
    AuditSlotId,
    AuditSlotKey,
    AuditVerdict,
    InstallationVersion,
    KillReason,
    RecipeExecutionId,
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
)
from autoskillit.pipeline._audit_admission_ledger import (
    _authority,
    _connections,
    _installations,
    _prepare,
    _recovery,
    _reservations,
)

__all__ = ["DefaultAuditAdmissionLedger"]

_HEAD_KEY_DOMAIN = "autoskillit:audit-admission:head-key:v1:sha256"
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

    @property
    def store_authority(self) -> AuditAdmissionStoreAuthority:
        return self._authority

    # -- connection/schema -------------------------------------------------
    # Connection plumbing lives in ``_audit_admission_ledger._connections``.
    # The facade delegates to ``_connections.open(...)``, ``commit(...)``, and
    # ``rollback(...)``; it does not own connection-open validation, identity
    # checks, or the explicit COMMIT/ROLLBACK primitives.

    # -- health/recovery -----------------------------------------------------

    def store_health(self) -> AuditAdmissionStoreHealth:
        with self._fence:
            return self._store_health

    def recover_all(self) -> AuditAdmissionRecoveryResult:
        with self._fence:
            connection: sqlite3.Connection | None = None
            try:
                connection = _connections.open(self._authority, self._busy_timeout_ms)
                installations, attempts = _recovery._read_installations_and_attempts(connection)
            except AuditAdmissionStorageError as exc:
                return self._fail_closed_recovery(exc.reason, exc.reason_code)
            except (OSError, sqlite3.Error) as exc:
                reason, reason_code = _recovery._classify_io_failure(exc)
                return self._fail_closed_recovery(reason, reason_code)
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
        return _connections.open(self._authority, self._busy_timeout_ms)

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
        from autoskillit.pipeline._audit_admission_ledger._encoders import (
            _HANDLE_DIGEST_DOMAIN as _handle_digest_domain,
        )
        from autoskillit.pipeline._audit_admission_ledger._encoders import (
            _HANDLE_PREFIX as _handle_prefix,
        )

        parts = reservation_handle.split(".")
        if len(parts) != 3 or parts[0] != _handle_prefix:
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
                    f"{_handle_digest_domain}:{compute_bytes_hash(secret.encode('utf-8'))}"
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
                    _connections.commit(connection)
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
                    _connections.commit(connection)
                    return
                connection.execute(
                    "INSERT INTO finalization_effects("
                    "attempt_id, effect_name, result_json, acknowledged_at"
                    ") VALUES (?, ?, ?, ?)",
                    (attempt_id.value, effect_name, result_json, _now_iso()),
                )
                _connections.commit(connection)
            except BaseException:
                _connections.rollback(connection)
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
                _connections.commit(connection)
                return outcome
            except BaseException:
                _connections.rollback(connection)
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
        installation = _installations._installation_row(connection, request.recipe_execution_id)
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
