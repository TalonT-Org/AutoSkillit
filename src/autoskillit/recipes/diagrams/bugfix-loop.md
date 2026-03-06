<!-- autoskillit-recipe-hash: sha256:ce9f547b6d2bc0926dcc9fd9e3a341777efc0fc600b8d59f8ddae00d1769d5fc -->
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
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.audit is false⟩
┌─ audit_impl  [run_skill]
│  ├─ ${{ result.verdict }} == GO  → merge
│  ├─ result.error  → escalate
│  ├─ (default)  → remediate
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ remediate  [route]
│  ✓ success  → plan ↑
│  ↺ ×3  → escalate
│
┌─ merge  [merge_worktree]
│  ✓ success  → done
│  ✗ failure  → escalate
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
