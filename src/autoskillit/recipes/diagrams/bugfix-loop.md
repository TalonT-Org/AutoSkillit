<!-- autoskillit-recipe-hash: sha256:328f5300282769e0e0a84d888fea4ecf9d61afb72212dcef641c70f772178e3d -->
<!-- autoskillit-diagram-format: v7 -->
## bugfix-loop
End-to-end test with automatic bug fixing in isolated worktrees.

### Graph
reset  [reset_test_dir] (retry ×3)
│  ↓ success → test
│  ✗ failure → escalate
│
test  [test_check] (retry ×3)
│  ↓ success → done
│  ✗ failure → investigate
│
investigate  [run_skill] (retry ×3)
│  ↓ success → plan
│  ✗ failure → escalate
│
plan  [run_skill] (retry ×3)
│  ↓ success → implement
│  ✗ failure → escalate
│
implement  [run_skill] (retry ×∞)
│  ↓ success → verify
│  ✗ failure → escalate
│  ⌛ context limit → retry_worktree
│
retry_worktree  [run_skill] (retry ×3)
│  ↓ success → verify
│  ✗ failure → escalate
│
verify  [test_check] (retry ×3)
│  ↓ success → audit_impl
│  ✗ failure → assess
│
assess  [run_skill] (retry ×3)
│  ↓ success → verify ↑
│  ✗ failure → classify
│
classify  [classify_fix] (retry ×3)
│  ${{ result.restart_scope }} == full_restart → investigate ↑
│  result.error → escalate
│  (default) → implement ↑
│  ✗ failure → escalate
│
├── [audit_impl] (retry ×3)  ← only if inputs.audit
│       ${{ result.verdict }} == GO → merge
│       result.error → escalate
│       (default) → remediate
│       ✗ failure → escalate
│
remediate  [route] (retry ×3)
│  ↓ success → plan ↑
│
merge  [merge_worktree] (retry ×3)
│  result.failed_step == 'dirty_tree' → assess ↑
│  result.failed_step == 'test_gate' → assess ↑
│  result.failed_step == 'post_rebase_test_gate' → assess ↑
│  result.failed_step == 'rebase' → assess ↑
│  result.error → escalate
│  (default) → done
│  ✗ failure → escalate
│
─────────────────────────────────────
done  "All tests passing. Fix merged successfully."
escalate  "Human intervention needed. Review the latest output for details."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| test_dir | Directory containing the project to test | — |
| base_branch | Base branch to merge fixes into (defaults to main) | main |
| helper_dir | Directory for helper agent sessions | — |
| audit | Gate merge on audit-impl quality check (true/false) | on |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code changes and investigation happen through headless sessions via run_skill.
- Route to on_failure when a step fails — the downstream skill (e.g., resolve-failures) has diagnostic access that the orchestrator does not. Do not investigate or attempt to fix failures directly.
- SEQUENTIAL EXECUTION: complete full cycle per part before advancing. For each plan_part, run the full cycle (implement → test → merge) before starting the next part. Do NOT batch-implement all parts upfront.
