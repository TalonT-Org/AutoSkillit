"""Read-only helpers for the audit admission ledger.

Three free functions:

- ``_current_head_read(connection, *, recipe_execution_id, cycle_id,
  scope_id, part_id)`` — returns the current ``AuditCycleHead`` for the
  given (cycle, scope, part) tuple, or ``None``.
- ``_head_by_key_read(connection, head_key)`` — same, keyed by an
  already-computed head key. Used by the write shards to re-read the
  current head inside their ``BEGIN IMMEDIATE`` block.
- ``_preflight_projection_read(connection, *, recipe_execution_id,
  installation_version, step_name)`` — returns the preflight projection
  for the given (recipe, installation, step), or ``None``.

All run under ``try/finally`` only (no ``BEGIN IMMEDIATE``, no
``ROLLBACK``).
"""

from __future__ import annotations

import sqlite3

from autoskillit.core import (
    AuditCycleHead,
    AuditPreflightProjection,
    InstallationVersion,
    RecipeExecutionId,
)
from autoskillit.pipeline._audit_admission_ledger._encoders import (
    _head_from_dict,
    _head_key,
    _json_loads,
)

__all__ = ["_current_head_read", "_head_by_key_read", "_preflight_projection_read"]


def _current_head_read(
    connection: sqlite3.Connection,
    *,
    recipe_execution_id: RecipeExecutionId,
    cycle_id: str,
    scope_id: str,
    part_id: str,
) -> AuditCycleHead | None:
    head_key = _head_key(recipe_execution_id, cycle_id, scope_id, part_id)
    return _head_by_key_read(connection, head_key)


def _head_by_key_read(
    connection: sqlite3.Connection,
    head_key: str,
) -> AuditCycleHead | None:
    row = connection.execute(
        "SELECT head_json FROM head_claims WHERE head_key = ?",
        (head_key,),
    ).fetchone()
    return _head_from_dict(_json_loads(row[0])) if row is not None else None


def _preflight_projection_read(
    connection: sqlite3.Connection,
    *,
    recipe_execution_id: RecipeExecutionId,
    installation_version: InstallationVersion,
    step_name: str,
) -> AuditPreflightProjection | None:
    row = connection.execute(
        "SELECT plan_set_id, scope_id, part_id FROM preflight_projections "
        "WHERE recipe_execution_id = ? AND installation_version = ? AND step_name = ?",
        (recipe_execution_id.value, installation_version.value, step_name),
    ).fetchone()
    if row is None:
        return None
    return AuditPreflightProjection(plan_set_id=row[0], scope_id=row[1], part_id=row[2])
