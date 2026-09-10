"""Backward-compat shim for ``recipe/_cmd_rpc_merge.py``.

Real implementation: ``autoskillit.recipe.cmd_rpc._cmd_rpc_merge`` (#4671 D).
Preserves old import path ``autoskillit.recipe._cmd_rpc_merge``.
"""

from __future__ import annotations

from autoskillit.recipe.cmd_rpc._cmd_rpc_merge import (
    Path,
    _attempt_rebase_with_conflict_report,
    _detect_remote,
    _write_rebase_conflict_report,
    advance_queue_pr,
    annotations,
    atomic_write,
    attempt_cheap_rebase,
    create_persistent_integration,
    direct_merge_conflict_fix,
    force_push_and_wait_mergeability,
    get_logger,
    immediate_merge_conflict_fix,
    logger,
    proactive_rebase_next_pr,
    queue_ejected_fix,
    review_path_rebase,
    run_gh,
    run_git,
    truncate_text,
    wait_for_direct_merge,
    wait_for_immediate_merge,
    wait_for_review_pr_mergeability,
)

__all__ = [
    "Path",
    "_attempt_rebase_with_conflict_report",
    "_detect_remote",
    "_write_rebase_conflict_report",
    "advance_queue_pr",
    "annotations",
    "atomic_write",
    "attempt_cheap_rebase",
    "create_persistent_integration",
    "direct_merge_conflict_fix",
    "force_push_and_wait_mergeability",
    "get_logger",
    "immediate_merge_conflict_fix",
    "logger",
    "proactive_rebase_next_pr",
    "queue_ejected_fix",
    "review_path_rebase",
    "run_gh",
    "run_git",
    "truncate_text",
    "wait_for_direct_merge",
    "wait_for_immediate_merge",
    "wait_for_review_pr_mergeability",
]
