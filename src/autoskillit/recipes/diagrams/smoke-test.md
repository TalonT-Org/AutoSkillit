<!-- autoskillit-recipe-hash: sha256:c2d42ab2bc8546d77ec5507b07bc3b6797f2aa600271c309b14124dda0044c0b -->
<!-- autoskillit-diagram-format: v3 -->
## smoke-test
End-to-end smoke test exercising the full orchestration path — script loading, step routing, tool dispatch, capture/context threading, retry logic, bugfix loop, and merge.

**Flow:** setup > seed_task > set_feature_branch > create_branch? > investigate > rectify > implement > set_worktree_path > test > merge > check_summary > create_summary? > done (assess/classify loop on failure)

### Graph
setup  [run_cmd] (retry ×3)
│  ↓ success → investigate
│  ✗ failure → escalate
│
┌────┤ FOR EACH:
│  investigate  [run_skill] (retry ×3)
│  │  ↓ success → rectify
│  │  ✗ failure → escalate
│  │
│  rectify  [run_skill] (retry ×3)
│  │  ↓ success → implement
│  │  ✗ failure → escalate
│  │
│  implement  [run_skill] (retry ×2)
│  │  ↓ success → test
│  │  ✗ failure → escalate
│  │
│  test  [test_check] (retry ×3)
│  │  ↓ success → push_feature_branch
│  │  ✗ failure → assess
│  │
│  ├── [push_feature_branch] (retry ×3)  ← only if inputs.collect_on_branch
│  │       ✗ failure → escalate
│  │
│  assess  [run_skill] (retry ×2)
│  │  ↓ success → test ↑
│  │  ✗ failure → classify
│  │
│  classify  [classify_fix] (retry ×3)
│  │  ${{ result.restart_scope }} == full_restart → investigate ↑
│  │  result.error → escalate
│  │  (default) → implement ↑
└────┘
│
merge  [merge_worktree] (retry ×3)
│  ↓ success → check_summary
│  ✗ failure → escalate
│
check_summary  [autoskillit.smoke_utils.check_bug_report_non_empty] (retry ×3)
│  ${{ result.non_empty }} == true → create_summary
│  (default) → done
│
├── [create_summary] (retry ×3)  ← only if inputs.collect_on_branch
│       ✗ failure → escalate
│
─────────────────────────────────────
⏹ done  "Smoke pipeline completed successfully."
⏹ escalate  "Smoke pipeline failed — check step output for details."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| workspace | Absolute path to temp workspace directory (must be a git repo with initial commit) | — |
| base_branch | Branch to merge into (overridden to feature branch when collect_on_branch is true) | main |
| collect_on_branch | Collect all fixes on a feature branch and create issue+PR at end (true/false) | on |
| original_base_branch | The original base branch for PR target (set automatically from base_branch when collect_on_branch is true) | main |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix directly from the orchestrator.
