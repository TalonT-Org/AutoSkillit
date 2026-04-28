<!-- autoskillit-recipe-hash: sha256:0000000000000000000000000000000000000000000000000000000000000000 -->
<!-- autoskillit-diagram-format: v7 -->
## remediation
Investigate, rectify, implement, and merge a bug fix with CI and PR gates.

### Graph
clone → get_issue_title → claim_issue → compute_branch
|
+-- create_branch → push_merge_target
|
investigate → rectify → review (optional)
|
dry_walkthrough → implement ↔ [retry_worktree on context limit]
|
test → assess
|
+-- audit_impl → remediate (optional)
|
make_plan → commit_guard → merge → push
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
