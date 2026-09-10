"""Backward-compat shim for ``recipe/_cmd_rpc.py``.

Real implementation: ``autoskillit.recipe.cmd_rpc._cmd_rpc`` (#4671 D).
Preserves old import path ``autoskillit.recipe._cmd_rpc``.
"""

from __future__ import annotations

from autoskillit.recipe.cmd_rpc._cmd_rpc import (
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
)

__all__ = [
    "advance_queue_pr",
    "attempt_cheap_rebase",
    "batch_create_issues",
    "check_dropped_ci_loop",
    "check_dropped_healthy_loop",
    "check_eject_limit",
    "commit_guard",
    "compute_branch",
    "create_audit_run_dir",
    "create_persistent_integration",
    "direct_merge_conflict_fix",
    "emit_fallback_map",
    "ensure_results",
    "export_local_bundle",
    "force_push_and_wait_mergeability",
    "immediate_merge_conflict_fix",
    "main_repo_guard",
    "proactive_rebase_next_pr",
    "queue_ejected_fix",
    "refetch_issues",
    "review_path_rebase",
    "verify_plan_artifacts",
    "wait_for_direct_merge",
    "wait_for_immediate_merge",
    "wait_for_review_pr_mergeability",
]
