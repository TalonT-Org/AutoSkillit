"""Finalization effect helpers for the audit admission ledger.

The shard preserves the load-bearing 4-field idempotent integrity check
on lines 1437-1442 of the pre-split implementation:

    row[1] != outcome_json
    or row[2] != required_effect_names_json
    or row[3] != outcome_json
    or row[4] != replay_projection
"""

from __future__ import annotations

import sqlite3
from typing import Any

from autoskillit.core import (
    AuditAdmissionStorageError,
    AuditAdmissionStorageFailureReason,
    AuditAttemptId,
    AuditAttemptLifecycle,
)
from autoskillit.pipeline._audit_admission_ledger._encoders import _json_loads, _now_iso

__all__ = [
    "_validate_finalization_effect_name",
    "_require_finalization_effect_lifecycle",
    "_finalization_effect_result_read",
    "_acknowledge_finalization_effect_locked",
    "_finalize_response_locked",
]


def _validate_finalization_effect_name(effect_name: str) -> None:
    if not isinstance(effect_name, str) or not effect_name.strip():
        raise ValueError("finalization effect name must be a non-empty string")


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


def _finalization_effect_result_read(
    connection: sqlite3.Connection,
    attempt_id: AuditAttemptId,
    effect_name: str,
    *,
    allowed_lifecycles: frozenset[AuditAttemptLifecycle],
) -> dict[str, Any] | None:
    _require_finalization_effect_lifecycle(
        connection,
        attempt_id,
        operation="finalization_effect_result",
        allowed_lifecycles=allowed_lifecycles,
    )
    row = connection.execute(
        "SELECT result_json FROM finalization_effects WHERE attempt_id = ? AND effect_name = ?",
        (attempt_id.value, effect_name),
    ).fetchone()
    return None if row is None else _json_loads(row[0])


def _acknowledge_finalization_effect_locked(
    connection: sqlite3.Connection,
    attempt_id: AuditAttemptId,
    effect_name: str,
    result_json: str,
    *,
    allowed_lifecycles: frozenset[AuditAttemptLifecycle],
) -> None:
    _require_finalization_effect_lifecycle(
        connection,
        attempt_id,
        operation="acknowledge_finalization_effect",
        allowed_lifecycles=allowed_lifecycles,
    )
    row = connection.execute(
        "SELECT result_json FROM finalization_effects WHERE attempt_id = ? AND effect_name = ?",
        (attempt_id.value, effect_name),
    ).fetchone()
    if row is not None:
        if row[0] != result_json:
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.INTEGRITY,
                "finalization-effect-result-mismatch",
            )
        return
    connection.execute(
        "INSERT INTO finalization_effects(attempt_id, effect_name, result_json, acknowledged_at) "
        "VALUES (?, ?, ?, ?)",
        (attempt_id.value, effect_name, result_json, _now_iso()),
    )


def _finalize_response_locked(
    connection: sqlite3.Connection,
    attempt_id: AuditAttemptId,
    *,
    outcome_json: str,
    required_effect_names_json: str,
    replay_projection: str,
    normalized_effect_names: tuple[str, ...],
) -> None:
    """In-transaction body of finalize_response.

    The facade wrapper calls ``_connections.commit`` after the shard
    returns (Decision 4 — explicit COMMIT on both branches).
    """
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
        "INSERT INTO response_commits(attempt_id, required_effect_names_json, outcome_json, "
        "replay_projection_json, committed_at) VALUES (?, ?, ?, ?, ?)",
        (
            attempt_id.value,
            required_effect_names_json,
            outcome_json,
            replay_projection,
            _now_iso(),
        ),
    )
    connection.execute(
        "UPDATE attempts SET lifecycle = ?, committed_outcome_json = ? WHERE attempt_id = ?",
        (
            AuditAttemptLifecycle.RESPONSE_COMMITTED.value,
            outcome_json,
            attempt_id.value,
        ),
    )
