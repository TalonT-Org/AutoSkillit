<!-- autoskillit-recipe-hash: sha256:e78fe272d401e61b90dd3cc427b7f55031e424f5ab88cc344f9ce93a0a71aa11 -->
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
+-- audit_impl → reset_test_fix_counter → reset_merge_test_fix_counter
    → reset_ref_push_counter → remediate (optional)
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
