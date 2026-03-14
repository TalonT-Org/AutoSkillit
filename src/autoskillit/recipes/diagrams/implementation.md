<!-- autoskillit-recipe-hash: sha256:7b92fe8b75d159ed9f1318163c455912b878c286590ea4182a01bb34e9ebc072 -->
<!-- autoskillit-diagram-format: v7 -->
## implementation
Plan, verify, implement, test, and merge a task end-to-end. Use when user says "run pipeline", "implement task", or "auto implement".

### Graph

```
      make-plan
      |
      +-- [review-approach] (optional)
      |
 +----+ FOR EACH PLAN PART:
 |    |
 |    dry-walkthrough --- implement --- test <-> [x fail -> fix]
 |
 +----+
      |
      +-- [audit] (optional)
      |     x fail [-> make-plan]
      |
      +-- [open-pr] (optional)
```

### Inputs

| Name | Description | Default |
|------|-------------|---------|
| task | What to implement | -- |
| source_dir | Remote URL for source repo | auto-detect |
| run_name | Pipeline run name prefix | impl |
| base_branch | Merge target | main |
| review_approach | Research approaches first | off |
| audit | Post-merge quality gate | on |
| open_pr | PR instead of direct merge | on |
| issue_url | GitHub issue to close on merge | -- |

Agent-managed: work_dir, remote_url, base_sha, merge_target, plan_path, all_plan_paths, plan_parts, review_path, worktree_path, remediation_path, issue_number, pr_number
