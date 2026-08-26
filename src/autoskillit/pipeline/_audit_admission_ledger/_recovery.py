"""Stateless sub-helpers consumed by ``DefaultAuditAdmissionLedger.recover_all``.

``recover_all`` and ``_fail_closed_recovery`` stay on the facade because they
mutate the facade's ``_recovered`` / ``_store_health`` state and own the
``threading.RLock`` ``_fence``. The two helpers below are pure functions
that the facade calls inside its ``recover_all`` transaction-shaped block.

The double-``except`` discipline in ``recover_all`` (``except
AuditAdmissionStorageError`` then ``except (OSError, sqlite3.Error)``) is
load-bearing — the ``TestRecovery`` test class depends on both arms.
``_classify_io_failure`` exists only so the second arm's classification
logic can be unit-tested in isolation; it does not change the catch
shape.
"""

from __future__ import annotations

import sqlite3

from autoskillit.core import (
    AuditAdmissionStorageFailureReason,
    AuditAttemptId,
    RecipeExecutionId,
)

__all__ = [
    "_read_installations_and_attempts",
    "_classify_io_failure",
]


def _read_installations_and_attempts(
    connection: sqlite3.Connection,
) -> tuple[tuple[RecipeExecutionId, ...], tuple[AuditAttemptId, ...]]:
    """Read active installations and all attempts in a single SELECT pair.

    Returns:
        A 2-tuple of ``(installations, attempts)``. The facade uses these
        to populate the ``AuditAdmissionRecoveryResult`` after a successful
        recovery. This helper is pure — it does not mutate the connection
        or the facade's state.
    """
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


def _classify_io_failure(
    exc: BaseException,
) -> tuple[AuditAdmissionStorageFailureReason, str]:
    """Classify an ``OSError``/``sqlite3.Error`` raised during recovery.

    Returns the ``(reason, reason_code)`` pair that the facade passes to
    ``_fail_closed_recovery``. Mirrors the pre-split inline classification:
    always report ``IO`` and include the exception class name in the
    reason code.
    """
    return (
        AuditAdmissionStorageFailureReason.IO,
        f"audit-admission-recovery-failed:{type(exc).__name__}",
    )
