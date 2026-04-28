<!-- autoskillit-recipe-hash: sha256:ed565e3970c6c0e0ca198a904be337c1b0160c39d3be5b13a2c19f310855299b -->
<!-- autoskillit-diagram-format: v7 -->
# implementation

```
      clone → get_issue_title → claim_issue → compute_branch
      |
      +-- create_branch (optional)
      |
      plan
      |
      +-- review (optional)
      |
 +----+ FOR EACH PLAN PART:
 |    |
 |    verify → implement → test ↔ [fix on failure]
 |    |
 |    merge → push → next_or_done
 |
 +----+
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
```
