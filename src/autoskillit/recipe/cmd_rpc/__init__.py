"""IL-2 recipe CLI/rpc dispatch implementations (#4671 Phase D).

Sub-package of :mod:`autoskillit.recipe`. Real implementations live in this
sub-package; backward-compat shims at the old ``recipe/_cmd_rpc*.py`` paths
preserve existing import sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._cmd_rpc import (
        advance_queue_pr,
        attempt_cheap_rebase,
        batch_create_issues,
        check_dropped_ci_loop,
        check_dropped_healthy_loop,
        check_eject_limit,
        commit_guard,
        compute_branch,
        create_audit_run_dir,
        create_persistent_integration,
        direct_merge_conflict_fix,
        emit_fallback_map,
        ensure_results,
        export_local_bundle,
        force_push_and_wait_mergeability,
        immediate_merge_conflict_fix,
        main_repo_guard,
        proactive_rebase_next_pr,
        queue_ejected_fix,
        refetch_issues,
        review_path_rebase,
        verify_plan_artifacts,
        wait_for_direct_merge,
        wait_for_immediate_merge,
        wait_for_review_pr_mergeability,
    )  # noqa: F401
    from ._cmd_rpc_guards import (  # noqa: F401
        _MAX_ASSOCIATION_FILES,
        _PLAN_ASSOCIATION_DOMAIN,
        _PLAN_ASSOCIATION_KEYS,
        AUDIT_CYCLE_SCHEMA_VERSION,
        ArtifactRef,
        AuditCycleVerifier,
        AuditVerdict,
        CommittedDispositionResolver,
        _check_regression,
        _count_lines_in_files,
        _count_numstat_net,
        _log_plan_disposition_rejection,
        _normalize_plan_parts,
        _parse_numstat_per_file,
        _RegressionVerdict,
        _resolve_plan_disposition,
        atomic_write,
        compute_canonical_hash,
        decode_versioned_json_bytes,
        get_logger,
        is_generated_path,
        logger,
        read_stable_contained_bytes,
        run_git,
    )
    from ._cmd_rpc_issues import (  # noqa: F401
        VANISHED_ERRORS,
        _ensure_and_resolve_labels,
        _extract_title,
        _resolve_repo_identity,
        _strip_ticket_body,
        _validate_mutation_variables,
        run_gh,
        scan_observed,
    )
    from ._cmd_rpc_merge import (
        _attempt_rebase_with_conflict_report,
        _detect_remote,
        _write_rebase_conflict_report,
        truncate_text,
    )  # noqa: F401


def __getattr__(name: str):
    """Lazily resolve symbols and submodules to avoid eager subpackage loads."""
    if name in _LAZY_MODULES:
        from importlib import import_module

        full = f"{__name__}.{name}"
        mod = import_module(full)
        globals()[name] = mod
        return mod
    mod_name = _LAZY_SYMBOL_TO_MODULE.get(name)
    if mod_name is not None:
        from importlib import import_module

        mod = import_module(f"{__name__}.{mod_name}")
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_LAZY_MODULES: frozenset[str] = frozenset(
    {
        "_cmd_rpc",
        "_cmd_rpc_guards",
        "_cmd_rpc_issues",
        "_cmd_rpc_merge",
    }
)

_LAZY_SYMBOL_TO_MODULE: dict[str, str] = {
    "advance_queue_pr": "_cmd_rpc_merge",
    "attempt_cheap_rebase": "_cmd_rpc_merge",
    "batch_create_issues": "_cmd_rpc_issues",
    "check_dropped_ci_loop": "_cmd_rpc_guards",
    "check_dropped_healthy_loop": "_cmd_rpc_guards",
    "check_eject_limit": "_cmd_rpc_guards",
    "commit_guard": "_cmd_rpc_guards",
    "compute_branch": "_cmd_rpc_guards",
    "create_audit_run_dir": "_cmd_rpc_issues",
    "create_persistent_integration": "_cmd_rpc_merge",
    "direct_merge_conflict_fix": "_cmd_rpc_merge",
    "emit_fallback_map": "_cmd_rpc_issues",
    "ensure_results": "_cmd_rpc_issues",
    "export_local_bundle": "_cmd_rpc_issues",
    "force_push_and_wait_mergeability": "_cmd_rpc_merge",
    "immediate_merge_conflict_fix": "_cmd_rpc_merge",
    "main_repo_guard": "_cmd_rpc_guards",
    "proactive_rebase_next_pr": "_cmd_rpc_merge",
    "queue_ejected_fix": "_cmd_rpc_merge",
    "refetch_issues": "_cmd_rpc_issues",
    "review_path_rebase": "_cmd_rpc_merge",
    "verify_plan_artifacts": "_cmd_rpc_guards",
    "wait_for_direct_merge": "_cmd_rpc_merge",
    "wait_for_immediate_merge": "_cmd_rpc_merge",
    "wait_for_review_pr_mergeability": "_cmd_rpc_merge",
    "AUDIT_CYCLE_SCHEMA_VERSION": "_cmd_rpc_guards",
    "ArtifactRef": "_cmd_rpc_guards",
    "AuditCycleVerifier": "_cmd_rpc_guards",
    "AuditVerdict": "_cmd_rpc_guards",
    "CommittedDispositionResolver": "_cmd_rpc_guards",
    "_MAX_ASSOCIATION_FILES": "_cmd_rpc_guards",
    "_PLAN_ASSOCIATION_DOMAIN": "_cmd_rpc_guards",
    "_PLAN_ASSOCIATION_KEYS": "_cmd_rpc_guards",
    "_RegressionVerdict": "_cmd_rpc_guards",
    "_check_regression": "_cmd_rpc_guards",
    "_count_lines_in_files": "_cmd_rpc_guards",
    "_count_numstat_net": "_cmd_rpc_guards",
    "_log_plan_disposition_rejection": "_cmd_rpc_guards",
    "_normalize_plan_parts": "_cmd_rpc_guards",
    "_parse_numstat_per_file": "_cmd_rpc_guards",
    "_resolve_plan_disposition": "_cmd_rpc_guards",
    "atomic_write": "_cmd_rpc_merge",
    "compute_canonical_hash": "_cmd_rpc_guards",
    "decode_versioned_json_bytes": "_cmd_rpc_guards",
    "get_logger": "_cmd_rpc_merge",
    "is_generated_path": "_cmd_rpc_guards",
    "logger": "_cmd_rpc_merge",
    "read_stable_contained_bytes": "_cmd_rpc_guards",
    "run_git": "_cmd_rpc_merge",
    "VANISHED_ERRORS": "_cmd_rpc_issues",
    "_ensure_and_resolve_labels": "_cmd_rpc_issues",
    "_extract_title": "_cmd_rpc_issues",
    "_resolve_repo_identity": "_cmd_rpc_issues",
    "_strip_ticket_body": "_cmd_rpc_issues",
    "_validate_mutation_variables": "_cmd_rpc_issues",
    "run_gh": "_cmd_rpc_merge",
    "scan_observed": "_cmd_rpc_issues",
    "_attempt_rebase_with_conflict_report": "_cmd_rpc_merge",
    "_detect_remote": "_cmd_rpc_merge",
    "_write_rebase_conflict_report": "_cmd_rpc_merge",
    "truncate_text": "_cmd_rpc_merge",
}

__all__ = [
    "AUDIT_CYCLE_SCHEMA_VERSION",
    "ArtifactRef",
    "AuditCycleVerifier",
    "AuditVerdict",
    "CommittedDispositionResolver",
    "VANISHED_ERRORS",
    "_MAX_ASSOCIATION_FILES",
    "_PLAN_ASSOCIATION_DOMAIN",
    "_PLAN_ASSOCIATION_KEYS",
    "_RegressionVerdict",
    "_attempt_rebase_with_conflict_report",
    "_check_regression",
    "_count_lines_in_files",
    "_count_numstat_net",
    "_detect_remote",
    "_ensure_and_resolve_labels",
    "_extract_title",
    "_log_plan_disposition_rejection",
    "_normalize_plan_parts",
    "_parse_numstat_per_file",
    "_resolve_plan_disposition",
    "_resolve_repo_identity",
    "_strip_ticket_body",
    "_validate_mutation_variables",
    "_write_rebase_conflict_report",
    "advance_queue_pr",
    "atomic_write",
    "attempt_cheap_rebase",
    "batch_create_issues",
    "check_dropped_ci_loop",
    "check_dropped_healthy_loop",
    "check_eject_limit",
    "commit_guard",
    "compute_branch",
    "compute_canonical_hash",
    "create_audit_run_dir",
    "create_persistent_integration",
    "decode_versioned_json_bytes",
    "direct_merge_conflict_fix",
    "emit_fallback_map",
    "ensure_results",
    "export_local_bundle",
    "force_push_and_wait_mergeability",
    "get_logger",
    "immediate_merge_conflict_fix",
    "is_generated_path",
    "logger",
    "main_repo_guard",
    "proactive_rebase_next_pr",
    "queue_ejected_fix",
    "read_stable_contained_bytes",
    "refetch_issues",
    "review_path_rebase",
    "run_gh",
    "run_git",
    "scan_observed",
    "truncate_text",
    "verify_plan_artifacts",
    "wait_for_direct_merge",
    "wait_for_immediate_merge",
    "wait_for_review_pr_mergeability",
]
