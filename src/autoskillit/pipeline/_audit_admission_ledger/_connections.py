"""SQLite connection plumbing for the audit admission ledger.

``open(authority, busy_timeout_ms)`` opens a connection and runs the
canonical boot sequence (``executescript(_SCHEMA_SQL)`` →
``_validate_metadata`` → ``_backfill_installation_occurrences`` →
``_validate_response_commit_integrity`` → identity re-check).
``commit`` / ``rollback`` are the explicit COMMIT/ROLLBACK primitives;
``rollback`` swallows ``sqlite3.Error`` (fail-open on rollback failure)
and this discipline is load-bearing — must not be relaxed.

The facade owns the per-instance fence and the recovery state; this
module does not know about the facade. Every helper is a free function
taking ``authority`` or ``connection`` as the first argument.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat

from autoskillit.core import (
    AuditAdmissionStorageError,
    AuditAdmissionStorageFailureReason,
    AuditAdmissionStoreAuthority,
    AuditAttemptLifecycle,
)
from autoskillit.pipeline._audit_admission_ledger._encoders import (
    _json_loads,
    _outcome_from_dict,
    _required_effect_names_from_json,
)
from autoskillit.pipeline._audit_admission_ledger._schema import (
    _DATABASE_MODE,
    _DIRECTORY_MODE,
    _METADATA_SCHEMA_VERSION,
    _SCHEMA_SQL,
)

__all__ = [
    "open",
    "_validate_database_target",
    "_database_identity",
    "_validate_metadata",
    "_backfill_installation_occurrences",
    "_validate_response_commit_integrity",
    "commit",
    "rollback",
]


def open(
    authority: AuditAdmissionStoreAuthority,
    busy_timeout_ms: int,
) -> sqlite3.Connection:
    """Open a connection and run the canonical boot sequence."""
    path = authority.database_path
    _validate_database_target(authority)
    before = _database_identity(authority)
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=rw",
        uri=True,
        isolation_level=None,
    )
    try:
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = EXTRA")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA_SQL)
        _validate_metadata(connection)
        _backfill_installation_occurrences(connection)
        _validate_response_commit_integrity(connection)
        if before != _database_identity(authority):
            raise AuditAdmissionStorageError(
                AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "audit-admission-store-identity-changed",
            )
    except BaseException:
        connection.close()
        raise
    return connection


def _validate_database_target(authority: AuditAdmissionStoreAuthority) -> None:
    path = authority.database_path
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
        or parent_stat.st_uid != authority.expected_owner_id
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
    _database_identity(authority)


def _database_identity(authority: AuditAdmissionStoreAuthority) -> tuple[int, int]:
    path = authority.database_path
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
        or path_stat.st_uid != authority.expected_owner_id
        or path_stat.st_nlink != 1
        or path_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (path_stat.st_dev, path_stat.st_ino) != (descriptor_stat.st_dev, descriptor_stat.st_ino)
    ):
        raise AuditAdmissionStorageError(
            AuditAdmissionStorageFailureReason.SECURITY_IDENTITY,
            "audit-admission-insecure-store-file",
        )
    return path_stat.st_dev, path_stat.st_ino


def _validate_metadata(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
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
            required_effect_names = _required_effect_names_from_json(required_effect_names_json)
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


def commit(connection: sqlite3.Connection) -> None:
    connection.execute("COMMIT")


def rollback(connection: sqlite3.Connection) -> None:
    """Issue ``ROLLBACK`` and swallow ``sqlite3.Error``.

    This swallow is intentional: if ROLLBACK itself fails because the
    connection is broken, do not shadow the original exception. The
    facade's ``except BaseException: self._rollback(connection); raise``
    relies on this discipline to preserve error propagation on
    concurrent-transaction crashes.
    """
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
