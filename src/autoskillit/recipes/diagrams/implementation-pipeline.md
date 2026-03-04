<!-- autoskillit-recipe-hash: sha256:36908a854e3d95a435e1e16b3bfea6d5edab531e267d83bdcfe6962b6ced41c5 -->
## implementation-pipeline
Plan, verify, implement, test, and merge a task end-to-end. Optionally decompose a large document into sequenced groups first. Use when user says "run pipeline", "implement task", or "auto implement".

**Flow:** clone > capture_base_sha > set_merge_target > (create_branch?) > (make-groups?) > make-plan > (review-approach?) > dry-walkthrough > implement > test > merge (per group, per plan part) > (audit-impl?) > (open_pr?) > push > cleanup

### Graph
Step                   Tool                   ✓ success              ✗ failure
───────────────────────────────────────────────────────────────────────
clone                  clone_repo             → capture_base_sha     → escalate_stop
  ↺ ×3 (failure)        → escalate
capture_base_sha       run_cmd                → set_merge_target     → escalate_stop
  ↺ ×3 (failure)        → escalate
set_merge_target       run_cmd                → create_branch        → escalate_stop
  ↺ ×3 (failure)        → escalate
create_branch          run_cmd                → push_merge_target    → cleanup_failure
  ↺ ×3 (failure)        → escalate
push_merge_target      push_to_remote         → group                → cleanup_failure
  ↺ ×3 (failure)        → escalate
group                  run_skill              → plan                 → cleanup_failure
  ↺ ×3 (failure)        → escalate
plan                   run_skill              → review               → cleanup_failure
  ↺ ×3 (failure)        → escalate
review                 run_skill              → verify               → cleanup_failure
  ↺ ×3 (failure)        → escalate
verify                 run_skill              → implement            → cleanup_failure
  ↺ ×3 (failure)        → escalate
implement              run_skill              → test                 → cleanup_failure
retry_worktree         run_skill              → test                 → cleanup_failure
  ↺ ×3 (failure)        → cleanup_failure
test                   test_check             → merge                → fix
  ↺ ×3 (failure)        → escalate
merge                  merge_worktree                                
  ↺ ×3 (failure)        → escalate
  result.failed_step == 'test_gate'  → fix
  result.failed_step == 'rebase'  → fix
  result.error          → cleanup_failure
  (default)             → next_or_done
push                   push_to_remote         → open_pr_step         → cleanup_failure
  ↺ ×3 (failure)        → escalate
fix                    run_skill              → test↑                → cleanup_failure
  ↺ ×3 (failure)        → escalate
next_or_done           route                                         
  ↺ ×3 (failure)        → escalate
  ${{ result.next }} == more_parts  → verify↑
  ${{ result.next }} == more_groups  → plan↑
  (default)             → audit_impl
audit_impl             run_skill                                     
  ↺ ×3 (failure)        → escalate
  ${{ result.verdict }} == GO  → push↑
  result.error          → escalate_stop
  (default)             → remediate
remediate              route                  → plan↑                
  ↺ ×3 (failure)        → escalate
open_pr_step           run_skill              → cleanup_success      → cleanup_failure
  ↺ ×3 (failure)        → escalate
cleanup_success        remove_clone           → done                 → done
  ↺ ×3 (failure)        → escalate
cleanup_failure        remove_clone           → escalate_stop        → escalate_stop
  ↺ ×3 (failure)        → escalate
───────────────────────────────────────────────────────────────────────
done  "Implementation pipeline complete. All groups/tasks have been planned, implemented, tested, and merged."
escalate_stop  "Pipeline failed — human intervention needed. Check the worktree and plan for details."

### Ingredients
| Name | Description | Required | Default |
|------|-------------|----------|---------|
| task | Description of what to implement (required when make_groups is false) | no |  |
| source_doc | Path to source document for group decomposition (required when make_groups is true) | no |  |
| source_dir | Path to the source repository to clone and work in. Leave empty to auto-detect from git rev-parse --show-toplevel.
 | no |  |
| run_name | Name prefix for this pipeline run (used in clone directory name) | no | impl |
| base_branch | Branch to merge into (defaults to current branch) | no | main |
| make_groups | Run /make-groups to decompose source_doc into sequenced implementation groups? (true/false) | no | false |
| review_approach | Run /review-approach before implementation? (true/false) | no | false |
| audit | Run /autoskillit:audit-impl once after all groups/parts have been merged, to check overall implementation quality and optionally trigger a remediation round (true/false) | no | true |
| open_pr | Create a feature branch (named from run_name) and open a GitHub PR to merge it into base_branch. The standard workflow — all worktree merges target the feature branch, then a PR is opened to base_branch. Set to false to merge directly into base_branch without a PR. (true/false) | no | true |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix directly from the orchestrator.
- When make_groups is false, task input is required.
- When make_groups is true, source_doc input is required.
- Process plan parts and groups sequentially. Complete the full cycle (verify → implement → test → merge) for one part before starting the next. Do NOT run verify for all parts upfront.
- By default (open_pr=true), a feature branch named from inputs.run_name is created. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
