"""Backward-compat shim for ``recipe/_cmd_rpc_issues.py``.

Real implementation: ``autoskillit.recipe.cmd_rpc._cmd_rpc_issues`` (#4671 D).
Preserves old import path ``autoskillit.recipe._cmd_rpc_issues``.
"""

from __future__ import annotations

import time  # noqa: F401  # re-exported for monkeypatch.setattr("autoskillit.recipe._cmd_rpc_issues.time", ...)

from autoskillit.recipe.cmd_rpc._cmd_rpc_issues import (
    UTC,
    VANISHED_ERRORS,
    Path,
    _ensure_and_resolve_labels,
    _extract_title,
    _resolve_repo_identity,
    _strip_ticket_body,
    _validate_mutation_variables,
    annotations,
    atomic_write,
    batch_create_issues,
    create_audit_run_dir,
    datetime,
    emit_fallback_map,
    ensure_results,
    export_local_bundle,
    fnmatch,
    get_logger,
    logger,
    refetch_issues,
    run_gh,
    scan_observed,
)

__all__ = [
    "Path",
    "UTC",
    "VANISHED_ERRORS",
    "_ensure_and_resolve_labels",
    "_extract_title",
    "_resolve_repo_identity",
    "_strip_ticket_body",
    "_validate_mutation_variables",
    "annotations",
    "atomic_write",
    "batch_create_issues",
    "create_audit_run_dir",
    "datetime",
    "emit_fallback_map",
    "ensure_results",
    "export_local_bundle",
    "fnmatch",
    "get_logger",
    "logger",
    "refetch_issues",
    "run_gh",
    "scan_observed",
]
