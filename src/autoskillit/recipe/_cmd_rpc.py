"""Recipe cmd externalization callables — run_python entry points (IL-006).

Re-export facade. Implementation: _cmd_rpc_guards.py, _cmd_rpc_merge.py, _cmd_rpc_issues.py.
"""

from autoskillit.recipe._cmd_rpc_guards import (  # noqa: F401
    check_dropped_healthy_loop,
    check_eject_limit,
    commit_guard,
    compute_branch,
    main_repo_guard,
)
from autoskillit.recipe._cmd_rpc_issues import (  # noqa: F401
    batch_create_issues,
    create_audit_run_dir,
    emit_fallback_map,
    ensure_results,
    export_local_bundle,
    refetch_issues,
)
from autoskillit.recipe._cmd_rpc_merge import (  # noqa: F401
    advance_queue_pr,
    attempt_cheap_rebase,
    create_persistent_integration,
    direct_merge_conflict_fix,
    force_push_and_wait_mergeability,
    immediate_merge_conflict_fix,
    proactive_rebase_next_pr,
    queue_ejected_fix,
    review_path_rebase,
    wait_for_direct_merge,
    wait_for_immediate_merge,
    wait_for_review_pr_mergeability,
)
