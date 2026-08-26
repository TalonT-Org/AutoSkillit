"""Stateless sub-helper consumed by ``DefaultAuditAdmissionLedger.recover_all``.

``recover_all`` and ``_fail_closed_recovery`` stay on the facade because they
mutate the facade's ``_recovered`` / ``_store_health`` state and own the
``threading.RLock`` ``_fence``.

The double-``except`` discipline in ``recover_all`` (``except
AuditAdmissionStorageError`` then ``except (OSError, sqlite3.Error)``) is
load-bearing — the ``TestRecovery`` test class depends on both arms.
"""

from __future__ import annotations

import sqlite3

from autoskillit.core import AuditAttemptId, RecipeExecutionId

__all__ = ["_read_installations_and_attempts"]


def _read_installations_and_attempts(
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
