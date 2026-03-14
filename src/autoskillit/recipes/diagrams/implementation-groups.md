<!-- autoskillit-recipe-hash: sha256:ab779f00a8ce16f7700044f5becff349397599684060d42f17cdb445f4d68373 -->
<!-- autoskillit-diagram-format: v7 -->
## implementation-groups
Decompose a source document into sequenced implementation groups, then plan, verify, implement, test, and merge each group end-to-end. Use when you have a large document or roadmap to implement via make-groups.

**Flow:** clone > capture_base_sha > set_merge_target > (create_branch?) > make-groups > make-plan > (review-approach?) > dry-walkthrough > implement > test > merge (per group, per plan part) > (audit-impl?) > (open_pr?) > push > (review_pr?) > (ci_watch?) > cleanup

### Graph
clone  [clone_repo] (retry ×3)
│  ↓ success → get_issue_title
│  ✗ failure → escalate_stop
│
├── [get_issue_title] (retry ×3)  ← only if inputs.issue_url
│       ✗ failure → escalate_stop
│
├── [claim_issue] (retry ×3)  ← only if inputs.issue_url
│       ${{ result.claimed }} == true → create_branch
│       (default) → escalate_stop
│       ✗ failure → escalate_stop
│
├── [create_branch] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → release_issue_failure
│
├── [push_merge_target] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → release_issue_failure
│
group  [run_skill] (retry ×3)
│  ↓ success → plan
│  ✗ failure → release_issue_failure
│
┌────┤ FOR EACH GROUP / PLAN PART:
│    │
│    plan (retry ×3)
│     │
│     ✗ failure → release_issue_failure
│    review (retry ×3)
│     │
│     ✗ failure → release_issue_failure
│    verify (retry ×3)
│     │
│     ✗ failure → release_issue_failure
│    implement (retry ×∞)
│     │
│     ✗ failure → release_issue_failure
│     ⌛ context limit → retry_worktree
│    test (retry ×3)
│     │
│     ✗ failure → fix
│    merge (retry ×3)
│     │
│     ✗ failure → release_issue_failure
│     result.failed_step == 'dirty_tree' → fix
│     result.failed_step == 'test_gate' → fix
│     result.failed_step == 'post_rebase_test_gate' → fix
│     result.failed_step == 'rebase' → fix
│     result.error → release_issue_failure
│     (default) → next_or_done
│
└────┘
│         └── next_or_done: ${{ result.next }} == more_parts  → verify ↑
│                           ${{ result.next }} == more_groups  → plan ↑
│                           (default)  → audit_impl
│
├── [audit_impl] (retry ×3)  ← only if inputs.audit
│       ${{ result.verdict }} == GO → push ↑
│       result.error → escalate_stop
│       (default) → remediate
│       ✗ failure → escalate_stop
│
remediate  [route] (retry ×3)
│  ↓ success → plan ↑
│
├── [open_pr_step] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → release_issue_failure
│
├── [review_pr] (retry ×3)  ← only if inputs.open_pr
│       ${{ result.verdict }} == changes_requested → resolve_review
│       ${{ result.verdict }} == needs_human → ci_watch
│       true → ci_watch
│       ✗ failure → resolve_review
│
resolve_review  [run_skill] (retry ×2)
│  ↓ success → re_push_review
│  ✗ failure → release_issue_failure
│
re_push_review  [push_to_remote] (retry ×3)
│  ↓ success → ci_watch
│  ✗ failure → release_issue_failure
│
├── [ci_watch] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → diagnose_ci
│
├── [diagnose_ci] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → resolve_ci
│
resolve_ci  [run_skill] (retry ×2)
│  ↓ success → re_push
│  ✗ failure → release_issue_failure
│
re_push  [push_to_remote] (retry ×3)
│  ↓ success → ci_watch ↑
│  ✗ failure → release_issue_failure
│
├── [release_issue_success] (retry ×3)  ← only if inputs.issue_url
│       ✗ failure → confirm_cleanup
│
├── [release_issue_failure] (retry ×3)  ← only if inputs.issue_url
│       ✗ failure → cleanup_failure
│
❓ confirm_cleanup
│  ✓ yes  → delete_clone
│  ✗ no   → done
│
delete_clone  [remove_clone] (retry ×3)
│  ↓ success → done
│  ✗ failure → done
│
cleanup_failure  [remove_clone] (retry ×3)
│  ↓ success → escalate_stop
│  ✗ failure → escalate_stop
│
─────────────────────────────────────
done  "Implementation pipeline complete. All groups/tasks have been planned, implemented, tested, and merged."
escalate_stop  "Pipeline failed — human intervention needed. Check the worktree and plan for details."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| source_doc | Path to source document for group decomposition | — |
| source_dir | Remote URL for source repo (auto-detected from git origin if empty) | auto-detect |
| run_name | Pipeline run name prefix (used in branch and clone naming) | impl |
| base_branch | Base branch to merge into (defaults to main) | main |
| review_approach | Run /review-approach before implementation? (true/false) | off |
| audit | Gate merge on audit-impl quality check (true/false) | on |
| open_pr | Open a PR to base_branch instead of merging directly (true/false) | on |
| issue_url | GitHub issue URL to close on merge (optional) | auto-detect |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix directly from the orchestrator.
- source_doc is required — it provides the work via group decomposition.
- Process plan parts and groups sequentially. Complete the full cycle (verify → implement → test → merge) for one part before starting the next. Do NOT run verify for all parts upfront.
- By default (open_pr=true), a feature branch is created with a unique name derived from inputs.run_name and context.issue_number (e.g. impl/124) or a date suffix (e.g. impl/20260304) when no issue is available. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
