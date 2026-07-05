<!-- autoskillit-recipe-hash: sha256:8ff83b430c66857bccb910882b8f7d75d7e1b769f74c839a1907fd23725e5d7e -->
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