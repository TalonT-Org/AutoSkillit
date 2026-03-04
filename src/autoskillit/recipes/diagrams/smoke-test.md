<!-- autoskillit-recipe-hash: sha256:c2d42ab2bc8546d77ec5507b07bc3b6797f2aa600271c309b14124dda0044c0b -->
## smoke-test
End-to-end smoke test exercising the full orchestration path — script loading, step routing, tool dispatch, capture/context threading, retry logic, bugfix loop, and merge.

**Flow:** setup > seed_task > set_feature_branch > create_branch? > investigate > rectify > implement > set_worktree_path > test > merge > check_summary > create_summary? > done (assess/classify loop on failure)

### Graph
Step                   Tool                   ✓ success              ✗ failure
───────────────────────────────────────────────────────────────────────
setup                  run_cmd                → setup_remote         → escalate
  ↺ ×3 (failure)        → escalate
setup_remote           run_cmd                → seed_task            → escalate
  ↺ ×3 (failure)        → escalate
seed_task              run_cmd                → set_feature_branch   → escalate
  ↺ ×3 (failure)        → escalate
set_feature_branch     run_cmd                → create_branch        → escalate
  ↺ ×3 (failure)        → escalate
create_branch          run_cmd                → investigate          → escalate
  ↺ ×3 (failure)        → escalate
investigate            run_skill              → rectify              → escalate
  ↺ ×3 (failure)        → escalate
rectify                run_skill              → implement            → escalate
  ↺ ×3 (failure)        → escalate
implement              run_skill              → set_worktree_path    → escalate
  ↺ ×2 (failure)        → escalate
set_worktree_path      run_cmd                → test                 → escalate
  ↺ ×3 (failure)        → escalate
test                   test_check             → push_feature_branch  → assess
  ↺ ×3 (failure)        → escalate
push_feature_branch    push_to_remote         → merge                → escalate
  ↺ ×3 (failure)        → escalate
assess                 run_skill              → test↑                → classify
  ↺ ×2 (failure)        → classify
classify               classify_fix                                  
  ↺ ×3 (failure)        → escalate
  ${{ result.restart_scope }} == full_restart  → investigate↑
  result.error          → escalate
  (default)             → implement↑
merge                  merge_worktree         → check_summary        → escalate
  ↺ ×3 (failure)        → escalate
check_summary          autoskillit.smoke_utils.check_bug_report_non_empty                        
  ↺ ×3 (failure)        → escalate
  ${{ result.non_empty }} == true  → create_summary
  (default)             → done
create_summary         run_skill              → done                 → escalate
  ↺ ×3 (failure)        → escalate
───────────────────────────────────────────────────────────────────────
done  "Smoke pipeline completed successfully."
escalate  "Smoke pipeline failed — check step output for details."

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
