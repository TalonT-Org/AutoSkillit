<!-- autoskillit-recipe-hash: sha256:79860a378ea2255bf7d2d16b7b4dc89d7b87108341be5338d9c515f0e781f6d9 -->
<!-- autoskillit-diagram-format: v5 -->
## pr-merge-pipeline
Analyze open PRs, determine merge order, collapse them sequentially into an integration branch, and open a single review PR for human approval. Handles conflict resolution via plan+implement for complex PRs.

**Flow:** clone > setup_remote > analyze_prs > create_integration_branch > [loop per PR: merge_pr or (plan > verify > implement > test > merge_to_integration)] > push_integration_branch > collect_artifacts > check_impl_plans > (audit_impl?) > create_review_pr > cleanup

### Graph
clone  [autoskillit.workspace.clone.clone_repo] (retry ×3)
│  ↓ success → setup_remote
│  ✗ failure → escalate_stop
│
setup_remote  [run_cmd] (retry ×3)
│  ↓ success → check_integration_exists
│  ✗ failure → cleanup_failure
│
check_integration_exists  [run_cmd] (retry ×3)
│  ↓ success → analyze_prs
│  ✗ failure → confirm_create_integration
│
❓ confirm_create_integration
│  ✓ yes  → create_persistent_integration
│  ✗ no   → escalate_stop
│
create_persistent_integration  [run_cmd] (retry ×3)
│  ↓ success → analyze_prs
│  ✗ failure → cleanup_failure
│
analyze_prs  [run_skill] (retry ×3)
│  ↓ success → create_integration_branch
│  ✗ failure → cleanup_failure
│
create_integration_branch  [run_cmd] (retry ×3)
│  ↓ success → publish_integration_branch
│  ✗ failure → cleanup_failure
│
publish_integration_branch  [push_to_remote] (retry ×3)
│  ↓ success → merge_pr
│  ✗ failure → cleanup_failure
│
┌────┤ FOR EACH PLAN PART:
│    │
│    merge_pr (retry ×5) ─── plan (retry ×3) ─── verify (retry ×5) ─── implement (retry ×∞) ─── retry_worktree (retry ×3) ─── test (retry ×3) ─── merge_to_integration (retry ×3) ─── resolve_merge_conflicts (retry ×3) ─── retry_merge_after_resolution (retry ×3) ─── fix (retry ×3) ↑ ─── next_part_or_next_pr (retry ×3)
│     │
│     ✗ failure → cleanup_failure
│     ⌛ context limit → cleanup_failure
│     ${{ result.escalation_required }} == true → escalate_stop
│     ${{ result.needs_plan }} == true → plan
│     (default) → next_part_or_next_pr
│                             │
│                             ✗ failure → cleanup_failure
│                                                 │
│                                                 ✗ failure → cleanup_failure
│                                                 ⌛ context limit → cleanup_failure
│                                                                       │
│                                                                       ✗ failure → cleanup_failure
│                                                                       ⌛ context limit → retry_worktree
│                                                                                                │
│                                                                                                ✗ failure → cleanup_failure
│                                                                                                ⌛ context limit → cleanup_failure
│                                                                                                                              │
│                                                                                                                              ✗ failure → fix
│                                                                                                                                                  │
│                                                                                                                                                  ✗ failure → cleanup_failure
│                                                                                                                                                  ${{ result.state }} == worktree_intact_rebase_aborted → resolve_merge_conflicts
│                                                                                                                                                  ${{ result.failed_step }} == 'rebase' → cleanup_failure
│                                                                                                                                                  ${{ result.failed_step }} == 'test_gate' → cleanup_failure
│                                                                                                                                                  ${{ result.failed_step }} == 'post_rebase_test_gate' → cleanup_failure
│                                                                                                                                                  (default) → next_part_or_next_pr
│                                                                                                                                                                                      │
│                                                                                                                                                                                      ✗ failure → cleanup_failure
│                                                                                                                                                                                      ${{ result.escalation_required }} == true → cleanup_failure
│                                                                                                                                                                                      (default) → retry_merge_after_resolution
│                                                                                                                                                                                                                             │
│                                                                                                                                                                                                                             ✗ failure → cleanup_failure
│                                                                                                                                                                                                                                                                         │
│                                                                                                                                                                                                                                                                         ✗ failure → cleanup_failure
│                                                                                                                                                                                                                                                                         ⌛ context limit → cleanup_failure
│                                                                                                                                                                                                                                                                                              │
│                                                                                                                                                                                                                                                                                              more_parts → verify ↑
│                                                                                                                                                                                                                                                                                              more_prs → merge_pr ↑
│                                                                                                                                                                                                                                                                                              all_done → push_integration_branch
│
└────┘
│
push_integration_branch  [run_cmd] (retry ×3)
│  ↓ success → collect_artifacts
│  ✗ failure → cleanup_failure
│
collect_artifacts  [run_cmd] (retry ×3)
│  ↓ success → check_impl_plans
│  ✗ failure → check_impl_plans
│
check_impl_plans  [run_cmd] (retry ×3)
│  ${{ result.stdout | trim }} == 0 → create_review_pr
│  (default) → audit_impl
│  ✗ failure → audit_impl
│
├── [audit_impl] (retry ×3)  ← only if inputs.audit
│       GO → create_review_pr
│       NO GO → remediate
│       ✗ failure → cleanup_failure
│
remediate  [route] (retry ×3)
│  ↓ success → plan ↑
│
create_review_pr  [run_skill] (retry ×3)
│  ↓ success → ci_watch_pr
│  ✗ failure → cleanup_failure
│
ci_watch_pr  [run_cmd] (retry ×3)
│  ↓ success → confirm_cleanup
│  ✗ failure → cleanup_failure
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
⏹ done  "PR consolidation complete. Batch branch pushed, review PR opened targeting integration branch. Human review required before merging integration into main."
⏹ escalate_stop  "Pipeline failed — human intervention needed. Check the integration branch and temp/pr-merge-pipeline/ for details."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| source_dir | Path to the source repository to clone and work in | — |
| run_name | Name prefix for this pipeline run (used in clone directory name) | pr-merge |
| keep_clone_on_failure | Keep the clone directory when the pipeline fails (true/false) | off |
| upstream_branch | Branch to create base_branch from if it does not yet exist on the remote | main |
| audit | Run /autoskillit:audit-impl after all PRs are merged to check coherency (true/false) | on |
| plans_dir | Directory where collected plan files are stored for audit-impl | temp/pr-merge-pipeline |

Agent-managed: base_branch
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill and run_cmd.
- Route to on_failure when a step fails — do not investigate or fix directly.
- SEQUENTIAL LOOP: Process one PR at a time through the full merge cycle before advancing to the next PR. Never batch-assess all PRs before starting merges.
- SEQUENTIAL EXECUTION: complete full cycle (verify → implement → test → merge_to_integration) per plan part before advancing to the next part or PR.
- INTEGRATION BRANCH: Two distinct branches exist. inputs.base_branch is the PERMANENT branch (default: integration) that accumulates AI work across runs — it is never deleted. context.integration_branch is the PER-RUN batch branch (e.g. pr-batch/pr-merge-{ts}) created from base_branch for this pipeline run. All PR merges and worktree merges target context.integration_branch. The final review PR opens from context.integration_branch into inputs.base_branch.
- PR QUEUE: The agent reads context.pr_order_file once at analyze_prs and maintains an ordered queue. context.current_pr_index (agent-maintained, starts at 0) tracks progress. Advance the index after each successful merge or complex-pr cycle completes.
- COMPLEX PR PATH: When merge_pr returns needs_plan=true, context.task is set to the conflict_report_path. Pass context.task to make-plan. The plan+implement cycle creates a worktree from the integration branch and merges it back via merge_to_integration.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
