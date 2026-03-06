<!-- autoskillit-recipe-hash: sha256:353cd3eab98857fbb70e4f79cb0b6661312bd63d27ebd58c0ad1db51f41087f2 -->
<!-- autoskillit-diagram-format: v3 -->
## bugfix-loop
End-to-end test with automatic bug fixing in isolated worktrees.

**Flow:** 

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
┌────┤ FOR EACH:
│  plan  [run_skill] (retry ×3)
│  │  ↓ success → implement
│  │  ✗ failure → escalate
│  │
│  implement  [run_skill] (retry ×∞)
│  │  ↓ success → verify
│  │  ✗ failure → escalate
│  │  ⌛ context limit → retry_worktree
│  │
│  retry_worktree  [run_skill] (retry ×3)
│  │  ↓ success → verify
│  │  ✗ failure → escalate
│  │
│  verify  [test_check] (retry ×3)
│  │  ↓ success → audit_impl
│  │  ✗ failure → assess
│  │
│  assess  [run_skill] (retry ×3)
│  │  ↓ success → verify ↑
│  │  ✗ failure → classify
│  │
│  classify  [classify_fix] (retry ×3)
│  │  ${{ result.restart_scope }} == full_restart → investigate ↑
│  │  result.error → escalate
│  │  (default) → implement ↑
│  │
│  audit_impl  [run_skill] (retry ×3)
│  │  ${{ result.verdict }} == GO → merge
│  │  result.error → escalate
│  │  (default) → remediate
│  │
│  remediate  [route] (retry ×3)
│  │  ↓ success → plan ↑
└────┘
│
merge  [merge_worktree] (retry ×3)
│  ↓ success → done
│  ✗ failure → escalate
│
─────────────────────────────────────
⏹ done  "All tests passing. Fix merged successfully."
⏹ escalate  "Human intervention needed. Review the latest output for details."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| test_dir | Directory containing the project to test | — |
| base_branch | Branch to merge fixes into | main |
| helper_dir | Directory for helper agent sessions | — |
| audit | Run /autoskillit:audit-impl before merge to gate on implementation quality | on |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code changes and investigation happen through headless sessions via run_skill.
- Route to on_failure when a step fails — the downstream skill (e.g., resolve-failures) has diagnostic access that the orchestrator does not. Do not investigate or attempt to fix failures directly.
- SEQUENTIAL EXECUTION: complete full cycle per part before advancing. For each plan_part, run the full cycle (implement → test → merge) before starting the next part. Do NOT batch-implement all parts upfront.
