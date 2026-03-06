<!-- autoskillit-recipe-hash: sha256:02b965e606fd79e5bd327cc611f28a39458c0457e5efa853c149e8e2f100af26 -->
<!-- autoskillit-diagram-format: v2 -->
## smoke-test
End-to-end smoke test exercising the full orchestration path — script loading, step routing, tool dispatch, capture/context threading, retry logic, bugfix loop, and merge.

**Flow:** setup > seed_task > set_feature_branch > create_branch? > investigate > rectify > implement > set_worktree_path > test > merge > check_summary > create_summary? > done (assess/classify loop on failure)

### Graph
┌─ setup  [run_cmd]
│  ✓ success  → setup_remote
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ setup_remote  [run_cmd]
│  ✓ success  → seed_task
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ seed_task  [run_cmd]
│  ✓ success  → set_feature_branch
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ set_feature_branch  [run_cmd]
│  ✓ success  → create_branch
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.collect_on_branch is false⟩
┌─ create_branch  [run_cmd]
│  ✓ success  → investigate
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ investigate  [run_skill]
│  ✓ success  → rectify
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ rectify  [run_skill]
│  ✓ success  → implement
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ implement  [run_skill]
│  ✓ success  → set_worktree_path
│  ✗ failure  → escalate
│  ↺ ×2  → escalate
│
┌─ set_worktree_path  [run_cmd]
│  ✓ success  → test
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ test  [test_check]
│  ✓ success  → push_feature_branch
│  ✗ failure  → assess
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.collect_on_branch is false⟩
┌─ push_feature_branch  [push_to_remote]
│  ✓ success  → merge
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ assess  [run_skill]
│  ✓ success  → test ↑
│  ✗ failure  → classify
│  ↺ ×2  → classify
│
┌─ classify  [classify_fix]
│  ├─ ${{ result.restart_scope }} == full_restart  → investigate ↑
│  ├─ result.error  → escalate
│  ├─ (default)  → implement ↑
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ merge  [merge_worktree]
│  ✓ success  → check_summary
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
┌─ check_summary  [autoskillit.smoke_utils.check_bug_report_non_empty]
│  ├─ ${{ result.non_empty }} == true  → create_summary
│  ├─ result.error  → escalate
│  ├─ (default)  → done
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.collect_on_branch is false⟩
┌─ create_summary  [run_skill]
│  ✓ success  → done
│  ✗ failure  → escalate
│  ↺ ×3  → escalate
│
───────────────────────────────────────
⏹ done  "Smoke pipeline completed successfully."
⏹ escalate  "Smoke pipeline failed — check step output for details."

### Ingredients
| Name | Description | Required | Default |
|------|-------------|----------|---------|
| workspace | Absolute path to temp workspace directory (must be a git repo with initial commit) | yes |  |
| base_branch | Branch to merge into (overridden to feature branch when collect_on_branch is true) | no | main |
| collect_on_branch | Collect all fixes on a feature branch and create issue+PR at end (true/false) | no | true |
| original_base_branch | The original base branch for PR target (set automatically from base_branch when collect_on_branch is true) | no | main |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix directly from the orchestrator.
