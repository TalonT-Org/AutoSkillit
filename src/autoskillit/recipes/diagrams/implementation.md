<!-- autoskillit-recipe-hash: sha256:04a976ae0115ca1280bba1db38f8a5066690aa1dc95927286b110c3ffaea136a -->
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
```
