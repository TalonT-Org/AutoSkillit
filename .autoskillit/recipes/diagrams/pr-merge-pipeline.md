<!-- autoskillit-recipe-hash: sha256:085833c62ea0319f46c09b9fcd3a00d3d1b10076e1b2d8f743a8f1ecf1836811 -->
<!-- autoskillit-diagram-format: v2 -->
## pr-merge-pipeline
Analyze open PRs, determine merge order, collapse them sequentially into an integration branch, and open a single review PR for human approval. Handles conflict resolution via plan+implement for complex PRs.

**Flow:** clone > setup_remote > analyze_prs > create_integration_branch > [loop per PR: merge_pr or (plan > verify > implement > test > merge_to_integration)] > push_integration_branch > collect_artifacts > audit_impl > create_review_pr > cleanup

### Graph
┌─ clone  [autoskillit.workspace.clone.clone_repo]
│  ✓ success  → setup_remote
│  ✗ failure  → escalate_stop
│  ↺ ×3  → escalate
│
┌─ setup_remote  [run_cmd]
│  ✓ success  → analyze_prs
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ analyze_prs  [run_skill]
│  ✓ success  → create_integration_branch
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ create_integration_branch  [run_cmd]
│  ✓ success  → merge_pr
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ merge_pr  [run_skill]
│  ├─ true  → plan
│  ├─ false  → next_part_or_next_pr
│  ✗ failure  → cleanup_failure
│  ↺ ×5  → cleanup_failure
│
┌─ plan  [run_skill]
│  ✓ success  → verify
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ verify  [run_skill]
│  ✓ success  → implement
│  ✗ failure  → cleanup_failure
│  ↺ ×5  → cleanup_failure
│
┌─ implement  [run_skill]
│  ✓ success  → test
│  ✗ failure  → cleanup_failure
│
┌─ retry_worktree  [run_skill]
│  ✓ success  → test
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → cleanup_failure
│
┌─ test  [test_check]
│  ✓ success  → merge_to_integration
│  ✗ failure  → fix
│  ↺ ×3  → escalate
│
┌─ merge_to_integration  [merge_worktree]
│  ✓ success  → next_part_or_next_pr
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ fix  [run_skill [sonnet]]
│  ✓ success  → test ↑
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → cleanup_failure
│
┌─ next_part_or_next_pr  [route]
│  ├─ more_parts  → verify ↑
│  ├─ more_prs  → merge_pr ↑
│  ├─ all_done  → push_integration_branch
│  ↺ ×3  → escalate
│
┌─ push_integration_branch  [run_cmd]
│  ✓ success  → collect_artifacts
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ collect_artifacts  [run_cmd]
│  ✓ success  → audit_impl
│  ✗ failure  → audit_impl
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.audit is false⟩
┌─ audit_impl  [run_skill]
│  ├─ GO  → create_review_pr
│  ├─ NO GO  → remediate
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ remediate  [route]
│  ✓ success  → plan ↑
│  ↺ ×3  → escalate
│
┌─ create_review_pr  [run_cmd]
│  ✓ success  → cleanup_success
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ cleanup_success  [remove_clone]
│  ✓ success  → done
│  ✗ failure  → done
│  ↺ ×3  → escalate
│
┌─ cleanup_failure  [remove_clone]
│  ✓ success  → escalate_stop
│  ✗ failure  → escalate_stop
│  ↺ ×3  → escalate
│
───────────────────────────────────────
⏹ done  "PR consolidation complete. Integration branch pushed, review PR opened. Human review required before merging to base_branch."
⏹ escalate_stop  "Pipeline failed — human intervention needed. Check the integration branch and temp/pr-merge-pipeline/ for details."

### Ingredients
| Name | Description | Required | Default |
|------|-------------|----------|---------|
| source_dir | Path to the source repository to clone and work in | yes |  |
| run_name | Name prefix for this pipeline run (used in clone directory name) | no | pr-merge |
| keep_clone_on_failure | Keep the clone directory when the pipeline fails (true/false) | no | false |
| base_branch | Target branch that all PRs are merging into; integration branch is created from this | no | main |
| audit | Run /autoskillit:audit-impl after all PRs are merged to check coherency (true/false) | no | true |
| plans_dir | Directory where collected plan files are stored for audit-impl | no | temp/pr-merge-pipeline |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Task, Explore, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill and run_cmd.
- Route to on_failure when a step fails — do not investigate or fix directly.
- SEQUENTIAL LOOP: Process one PR at a time through the full merge cycle before advancing to the next PR. Never batch-assess all PRs before starting merges.
- SEQUENTIAL EXECUTION: complete full cycle (verify → implement → test → merge_to_integration) per plan part before advancing to the next part or PR.
- INTEGRATION BRANCH: All PR merges and worktree merges target context.integration_branch, not inputs.base_branch. The base_branch is only used for the final review PR.
- PR QUEUE: The agent reads context.pr_order_file once at analyze_prs and maintains an ordered queue. context.current_pr_index (agent-maintained, starts at 0) tracks progress. Advance the index after each successful merge or complex-pr cycle completes.
- COMPLEX PR PATH: When merge_pr returns needs_plan=true, context.task is set to the conflict_report_path. Pass context.task to make-plan. The plan+implement cycle creates a worktree from the integration branch and merges it back via merge_to_integration.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
