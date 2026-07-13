<!-- autoskillit-recipe-hash: sha256:07605637c5979b8db209515a460ec474dcda1a834d371720bec9f6c35d49f76e -->
<!-- autoskillit-diagram-format: v7 -->
## remediation
Investigate, rectify, implement, and merge a bug fix with CI and PR gates.

### Graph
clone → get_issue_title → claim_issue → compute_branch
|
+-- create_branch → push_merge_target
|
investigate → rectify → review_approach (optional)
|
dry_walkthrough → implement ↔ [retry_worktree on context limit]
|
test ↔ assess
     [assess context/rate limit → test]
|
+-- audit_impl → reset_test_fix_counter → reset_merge_test_fix_counter
    → reset_ref_push_counter → pre_remediation_merge → remediate (optional)
    → merge_gate_test ↔ merge_gate_assess
       [merge_gate_assess context/rate limit → merge_gate_test]
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
