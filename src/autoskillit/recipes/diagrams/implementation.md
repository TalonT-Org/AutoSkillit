<!-- autoskillit-recipe-hash: sha256:31cc4041bae3dd8dfcbc7ab8518ba33c8aeeab99515f1181a273bd8812ae0e90 -->
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
