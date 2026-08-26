"""Disposition CAS helpers for the audit admission ledger.

The CAS gate is the Python-level comparison on the 4-tuple (the SQL
PK uniqueness is a separate concern).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from autoskillit.core import (
    AuditDispositionCommitOutcome,
    AuditDispositionCommitRequest,
)
from autoskillit.pipeline._audit_admission_ledger._encoders import (
    _head_key,
)
from autoskillit.pipeline._audit_admission_ledger._installations import _installation_row
from autoskillit.pipeline._audit_admission_ledger._reads import _head_by_key_read

__all__ = ["_commit_disposition_locked", "_resolve_disposition_read"]


def _commit_disposition_locked(
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
    installation = _installation_row(connection, request.recipe_execution_id)
    if (
        installation is None
        or installation.installation_version != request.installation_version.value
        or installation.retired
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
    current_head = _head_by_key_read(connection, head_key)
    if current_head is None or current_head.current_authority_digest != request.authority_digest:
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


def _resolve_disposition_read(
    connection: sqlite3.Connection,
    *,
    authority_digest: str,
    plan_digest: str,
) -> Path | None:
    row = connection.execute(
        "SELECT report_path FROM disposition_projections "
        "WHERE authority_digest = ? AND plan_digest = ? "
        "ORDER BY generated_at DESC LIMIT 1",
        (authority_digest, plan_digest),
    ).fetchone()
    return Path(row[0]) if row is not None else None
