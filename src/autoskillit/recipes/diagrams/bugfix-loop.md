<!-- autoskillit-recipe-hash: sha256:b2b1b3b7eaa852c5d983827a97341f1a86f8dbc49ec07eefcae3bffe23941b39 -->
<!-- autoskillit-diagram-format: v2 -->
## bugfix-loop
End-to-end test with automatic bug fixing in isolated worktrees.

**Flow:** 

### Graph
┌─ reset  [reset_test_dir]
│  ✓ success  → test
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ test  [test_check]
│  ✓ success  → done
│  ✗ failure  → investigate
│  ↺ ×3  → escalate
│
┌─ investigate  [run_skill]
│  ✓ success  → plan
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ plan  [run_skill]
│  ✓ success  → implement
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ implement  [run_skill]
│  ✓ success  → verify
│  ✗ failure  → escalate
│
┌─ retry_worktree  [run_skill]
│  ✓ success  → verify
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ verify  [test_check]
│  ✓ success  → audit_impl
│  ✗ failure  → assess
│  ↺ ×3  → escalate
│
┌─ assess  [run_skill]
│  ✓ success  → verify ↑
│  ✗ failure  → classify
│  ↺ ×3  → classify
│
┌─ classify  [classify_fix]
│  ├─ ${{ result.restart_scope }} == full_restart  → investigate ↑
│  ├─ result.error  → escalate
│  ├─ (default)  → implement ↑
│  ↺ ×3  → escalate
│
┌─ audit_impl  [run_skill]
│  ├─ ${{ result.verdict }} == GO  → merge
│  ├─ result.error  → escalate
│  ├─ (default)  → remediate
│  ↺ ×3  → escalate
│
┌─ remediate  [route]
│  ✓ success  → plan ↑
│  ↺ ×3  → escalate
│
┌─ merge  [merge_worktree]
│  ├─ result.failed_step == 'test_gate'  → assess ↑
│  ├─ result.failed_step == 'post_rebase_test_gate'  → assess ↑
│  ├─ result.failed_step == 'rebase'  → assess ↑
│  ├─ result.error  → escalate
│  ├─ (default)  → done
│  ↺ ×3  → escalate
│
───────────────────────────────────────
⏹ done  "All tests passing. Fix merged successfully."
⏹ escalate  "Human intervention needed. Review the latest output for details."

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
