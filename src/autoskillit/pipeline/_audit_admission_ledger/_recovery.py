"""Stateless SELECT helper used by ``DefaultAuditAdmissionLedger.recover_all``."""

from __future__ import annotations

import sqlite3

from autoskillit.core import AuditAttemptId, RecipeExecutionId

__all__ = ["_installations_and_attempts_read"]


def _installations_and_attempts_read(
    connection: sqlite3.Connection,
) -> tuple[tuple[RecipeExecutionId, ...], tuple[AuditAttemptId, ...]]:
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
    return installations, attempts
