"""Authority commit & preflight helpers for the audit admission ledger.

Single function consumed by the facade's ``commit_authority`` method:

- ``_commit_authority_locked(connection, request)`` — runs the
  in-transaction SELECT/INSERT/UPDATE sequence that advances an attempt
  from ``PREPARED`` to ``PUBLISHED_PENDING_FINALIZATION`` by writing
  the new head_claim and preflight_projections rows.

The shard preserves the pre-split implementation's reachability check:
when ``current_head is not None and current_head.verdict is
AuditVerdict.GO``, the function returns ``terminal_head`` and does NOT
write. This guard is load-bearing — a terminal head cannot be advanced,
and this is the only enforcement point (Interface Mapper F2 / Decision 4).

The idempotency path (``lifecycle in {PUBLISHED_PENDING_FINALIZATION,
RESPONSE_COMMITTED}`` returning ``committed=True`` without writes) is
also preserved verbatim.

The facade owns the ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``
boundary.
"""

from __future__ import annotations

import sqlite3

from autoskillit.core import (
    AuditAttemptLifecycle,
    AuditFinalCommitOutcome,
    AuditFinalCommitRequest,
    AuditVerdict,
    RecipeExecutionId,
)
from autoskillit.pipeline._audit_admission_ledger._encoders import (
    _head_from_dict,
    _head_to_dict,
    _json_dumps,
    _json_loads,
)
from autoskillit.pipeline._audit_admission_ledger._installations import _installation_row

__all__ = ["_commit_authority_locked"]


def _commit_authority_locked(
    connection: sqlite3.Connection,
    request: AuditFinalCommitRequest,
) -> AuditFinalCommitOutcome:
    row = connection.execute(
        "SELECT a.lifecycle, s.recipe_execution_id, s.installation_version, s.head_key "
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
    installation_row = _installation_row(
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
