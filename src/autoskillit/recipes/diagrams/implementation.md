<!-- autoskillit-recipe-hash: sha256:ad05070c74e59ce5721a6bb855ba3b2ea51d7557e92d5b7933527dbdca1eb595 -->
<!-- autoskillit-diagram-format: v7 -->
# implementation

```
      clone → get_issue_title → claim_issue → compute_branch
      |
      +-- create_branch (optional)
      |
      plan
      |
      +-- review_approach (optional)
      |
 +----+ FOR EACH PLAN PART:
 |    |
 |    verify → implement → test ↔ [fix on failure]
 |    |
 |    merge → push → next_or_done
 |
 +----+
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
```
