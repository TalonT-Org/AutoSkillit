"""Backward-compat shim for ``recipe/_cmd_rpc_merge.py``.

Real implementation: ``autoskillit.recipe.cmd_rpc._cmd_rpc_merge`` (#4671 D).
Preserves old import path ``autoskillit.recipe._cmd_rpc_merge``.
"""

from __future__ import annotations

import time  # noqa: F401  # re-exported for monkeypatch.setattr("autoskillit.recipe._cmd_rpc_merge.time", ...)

from autoskillit.recipe.cmd_rpc._cmd_rpc_merge import (
    Path,
    _attempt_rebase_with_conflict_report,
    _detect_remote,
    _write_rebase_conflict_report,
    annotations,
    atomic_write,
    attempt_cheap_rebase,
    create_persistent_integration,
    direct_merge_conflict_fix,
    first_ordered_pr,
    force_push_and_wait_mergeability,
    get_logger,
    immediate_merge_conflict_fix,
    logger,
    next_ordered_pr,
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
    "first_ordered_pr",
    "next_ordered_pr",
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
