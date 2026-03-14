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
