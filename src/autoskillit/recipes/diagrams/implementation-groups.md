<!-- autoskillit-recipe-hash: sha256:0f49b5fc315fc868772e622bb750e3e6e8e97720a95c66650380e928fd906b3b -->
<!-- autoskillit-diagram-format: v7 -->
## implementation-groups
Group-based implementation with per-group plan/implement/test cycles and PR gates.

### Graph
clone → get_issue_title → claim_issue → compute_branch
|
+-- create_branch → push_merge_target
|
group → plan → review_approach (optional)
|
┌────┤ FOR EACH PLAN PART:
│    verify → implement ↔ [retry_worktree on context limit]
│    |
│    test → commit_guard → merge → push
│    |
│    fix (on failure) → next_or_done
└────┘
|
+-- audit_impl → remediate (optional)
|
+-- [open-pr] (optional):
|     prepare_pr → compose_pr → review_pr → resolve_review
|     ci_watch → check_repo_merge_state
|     → [queue | direct | immediate] merge path
|     → diagnose_ci → resolve_ci (on CI failure)
|
release_issue_success / release_issue_failure
|
+-- patch_token_summary (optional)
|
register_clone_success / register_clone_failure
─────────────────────────────────────
done  "Complete."
escalate_stop  "Failed."
