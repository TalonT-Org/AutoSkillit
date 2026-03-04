<!-- autoskillit-recipe-hash: sha256:353cd3eab98857fbb70e4f79cb0b6661312bd63d27ebd58c0ad1db51f41087f2 -->
## bugfix-loop
End-to-end test with automatic bug fixing in isolated worktrees.

**Flow:** 

### Graph
Step                   Tool                   ✓ success              ✗ failure
───────────────────────────────────────────────────────────────────────
reset                  reset_test_dir         → test                 → escalate
  ↺ ×3 (failure)        → escalate
test                   test_check             → done                 → investigate
  ↺ ×3 (failure)        → escalate
investigate            run_skill              → plan                 → escalate
  ↺ ×3 (failure)        → escalate
plan                   run_skill              → implement            → escalate
  ↺ ×3 (failure)        → escalate
implement              run_skill              → verify               → escalate
retry_worktree         run_skill              → verify               → escalate
  ↺ ×3 (failure)        → escalate
verify                 test_check             → audit_impl           → assess
  ↺ ×3 (failure)        → escalate
assess                 run_skill              → verify↑              → classify
  ↺ ×3 (failure)        → classify
classify               classify_fix                                  
  ↺ ×3 (failure)        → escalate
  ${{ result.restart_scope }} == full_restart  → investigate↑
  result.error          → escalate
  (default)             → implement↑
audit_impl             run_skill                                     
  ↺ ×3 (failure)        → escalate
  ${{ result.verdict }} == GO  → merge
  result.error          → escalate
  (default)             → remediate
remediate              route                  → plan↑                
  ↺ ×3 (failure)        → escalate
merge                  merge_worktree         → done                 → escalate
  ↺ ×3 (failure)        → escalate
───────────────────────────────────────────────────────────────────────
done  "All tests passing. Fix merged successfully."
escalate  "Human intervention needed. Review the latest output for details."

### Ingredients
| Name | Description | Required | Default |
|------|-------------|----------|---------|
| test_dir | Directory containing the project to test | yes |  |
| base_branch | Branch to merge fixes into | no | main |
| helper_dir | Directory for helper agent sessions | yes |  |
| audit | Run /autoskillit:audit-impl before merge to gate on implementation quality | no | true |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code changes and investigation happen through headless sessions via run_skill.
- Route to on_failure when a step fails — the downstream skill (e.g., resolve-failures) has diagnostic access that the orchestrator does not. Do not investigate or attempt to fix failures directly.
- SEQUENTIAL EXECUTION: complete full cycle per part before advancing. For each plan_part, run the full cycle (implement → test → merge) before starting the next part. Do NOT batch-implement all parts upfront.
