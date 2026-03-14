<!-- autoskillit-recipe-hash: sha256:bc87a1c95d3a2891703a9b168bf6a75e931866ffbf7a4dad7d95e81d2715eed4 -->
<!-- autoskillit-diagram-format: v7 -->
## smoke-test
End-to-end smoke test exercising the full orchestration path — script loading, step routing, tool dispatch, capture/context threading, retry logic, bugfix loop, and merge.

**Flow:** setup > seed_task > set_feature_branch > create_branch? > investigate > rectify > implement > set_worktree_path > test > merge > check_summary > create_summary? > done (assess/classify loop on failure)

### Graph
setup  [run_cmd] (retry ×3)
│  ↓ success → investigate
│  ✗ failure → escalate
│
investigate  [run_skill] (retry ×3)
│  ↓ success → rectify
│  ✗ failure → escalate
│
rectify  [run_skill] (retry ×3)
│  ↓ success → implement
│  ✗ failure → escalate
│
implement  [run_skill] (retry ×2)
│  ↓ success → test
│  ✗ failure → escalate
│
test  [test_check] (retry ×3)
│  ↓ success → push_feature_branch
│  ✗ failure → assess
│
├── [push_feature_branch] (retry ×3)  ← only if inputs.collect_on_branch
│       ✗ failure → escalate
│
assess  [run_skill] (retry ×2)
│  ↓ success → test ↑
│  ✗ failure → classify
│
classify  [classify_fix] (retry ×3)
│  ${{ result.restart_scope }} == full_restart → investigate ↑
│  result.error → escalate
│  (default) → implement ↑
│  ✗ failure → escalate
│
commit_dirty  [run_cmd] (retry ×3)
│  ↓ success → merge
│  ✗ failure → escalate
│
merge  [merge_worktree] (retry ×3)
│  result.failed_step == 'dirty_tree' → commit_dirty ↑
│  result.failed_step == 'test_gate' → escalate
│  result.failed_step == 'post_rebase_test_gate' → escalate
│  result.failed_step == 'rebase' → escalate
│  result.error → escalate
│  (default) → check_summary
│  ✗ failure → escalate
│
check_summary  [autoskillit.smoke_utils.check_bug_report_non_empty] (retry ×3)
│  ${{ result.non_empty }} == true → create_summary
│  result.error → escalate
│  (default) → done
│  ✗ failure → escalate
│
├── [create_summary] (retry ×3)  ← only if inputs.collect_on_branch
│       ✗ failure → escalate
│
─────────────────────────────────────
done  "Smoke pipeline completed successfully."
escalate  "Smoke pipeline failed — check step output for details."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| workspace | Git repo workspace directory (must have an initial commit) | — |
| base_branch | Merge target branch (feature branch override when collect_on_branch=true) | main |
| collect_on_branch | Collect all fixes on a feature branch and create issue+PR at end (true/false) | on |
| original_base_branch | Original base branch for PR target (auto-set from base_branch) | main |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix directly from the orchestrator.
