"""Prepare transition helper for the audit admission ledger.

Single function consumed by the facade's ``prepare`` method:

- ``_prepare_locked(connection, request)`` — runs the in-transaction
  SELECT/UPDATE/INSERT sequence that advances an attempt from ``OPEN`` to
  either ``PREPARED`` or ``SEMANTIC_REJECTED`` (idempotent re-delivery of
  an already-``PREPARED`` attempt is also handled).

The early-return branches (``unknown_attempt``, ``installation_stale``,
the OPEN-reject path, the OPEN-accept path with its prepared_effects
INSERT loop, the PREPARED-idempotent path with its existing-effects
SELECT, and the terminal-lifecycle rejection) are load-bearing for
state-machine correctness.

The facade owns the ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``
boundary.
"""

from __future__ import annotations

import sqlite3

from autoskillit.core import (
    AuditAttemptLifecycle,
    AuditPrepareOutcome,
    AuditPrepareRequest,
    RecipeExecutionId,
)
from autoskillit.pipeline._audit_admission_ledger._installations import _installation_row_read

__all__ = ["_prepare_locked"]


def _prepare_locked(
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
    installation_row = _installation_row_read(connection, recipe_execution_id)
    if (
        installation_version != request.installation_version.value
        or installation_row is None
        or installation_row.installation_version != request.installation_version.value
        or installation_row.retired
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
