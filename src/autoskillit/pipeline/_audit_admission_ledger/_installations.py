"""Installation lifecycle helpers for the audit admission ledger.

Each ``_locked`` helper does only the in-transaction work; the facade
owns the ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK`` boundary.
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import NamedTuple

from autoskillit.core import (
    AuditAdmissionStorageError,
    AuditAdmissionStorageFailureReason,
    InstallationVersion,
    RecipeExecutionId,
)
from autoskillit.pipeline._audit_admission_ledger._encoders import _now_iso

__all__ = [
    "InstallationRow",
    "_create_or_get_installation_locked",
    "_retire_installation_locked",
    "_installation_row_read",
]


class InstallationRow(NamedTuple):
    installation_version: InstallationVersion
    retired: bool


def _create_or_get_installation_locked(
    connection: sqlite3.Connection,
    *,
    recipe_execution_id: RecipeExecutionId,
    snapshot_digest: str,
) -> InstallationVersion:
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
        return InstallationVersion(row[0])
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
    return version


def _retire_installation_locked(
    connection: sqlite3.Connection,
    *,
    recipe_execution_id: RecipeExecutionId,
    installation_version: InstallationVersion,
) -> None:
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


def _installation_row_read(
    connection: sqlite3.Connection,
    recipe_execution_id: RecipeExecutionId,
) -> InstallationRow | None:
    row = connection.execute(
        "SELECT installation_version, retired FROM installations WHERE recipe_execution_id = ?",
        (recipe_execution_id.value,),
    ).fetchone()
    if row is None:
        return None
    return InstallationRow(InstallationVersion(row[0]), bool(row[1]))
