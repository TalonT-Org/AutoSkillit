<!-- autoskillit-recipe-hash: sha256:ceed5681a207a471eed55e9fab1a8b359445e1114e87e27b93d1ad53f5e9609f -->
## audit-and-fix
Audit codebase, investigate findings, plan fixes, implement in a feature branch, and open a PR.

**Flow:** 

### Graph
Step                   Tool                   ✓ success              ✗ failure
───────────────────────────────────────────────────────────────────────
clone                  clone_repo             → set_merge_target     → escalate_stop
  ↺ ×3 (failure)        → escalate
set_merge_target       run_cmd                → create_branch        → escalate_stop
  ↺ ×3 (failure)        → escalate
create_branch          run_cmd                → push_merge_target    → cleanup_failure
  ↺ ×3 (failure)        → escalate
push_merge_target      push_to_remote         → audit                → cleanup_failure
  ↺ ×3 (failure)        → escalate
audit                  run_skill              → investigate          → cleanup_failure
  ↺ ×3 (failure)        → escalate
investigate            run_skill              → plan                 → cleanup_failure
  ↺ ×3 (failure)        → escalate
plan                   run_skill              → implement            → cleanup_failure
  ↺ ×3 (failure)        → escalate
implement              run_skill              → test                 → cleanup_failure
retry_worktree         run_skill              → test                 → cleanup_failure
  ↺ ×3 (failure)        → cleanup_failure
test                   test_check             → merge                → fix
  ↺ ×3 (failure)        → escalate
fix                    run_skill              → test↑                → cleanup_failure
  ↺ ×3 (failure)        → escalate
merge                  merge_worktree         → push                 → cleanup_failure
  ↺ ×3 (failure)        → escalate
push                   push_to_remote         → open_pr_step         → cleanup_failure
  ↺ ×3 (failure)        → escalate
open_pr_step           run_skill              → cleanup_success      → cleanup_failure
  ↺ ×3 (failure)        → escalate
cleanup_success        remove_clone           → done                 → done
  ↺ ×3 (failure)        → escalate
cleanup_failure        remove_clone           → escalate_stop        → escalate_stop
  ↺ ×3 (failure)        → escalate
───────────────────────────────────────────────────────────────────────
done  "Audit findings addressed. Changes merged via PR."
escalate_stop  "Human intervention needed."

### Ingredients
| Name | Description | Required | Default |
|------|-------------|----------|---------|
| source_dir | Path to the source repository to clone and work in. Leave empty to auto-detect from git rev-parse --show-toplevel.
 | no |  |
| run_name | Name prefix for this pipeline run (used in clone directory name and feature branch name) | no | audit-fix |
| base_branch | Branch to branch off of and PR target | no | main |
| audit_type | Type of audit to run (arch, tests, cohesion, defense-standards) | no | arch |
| open_pr | Create a feature branch (named from run_name) and open a GitHub PR to merge it into base_branch. The standard workflow — all worktree merges target the feature branch, then a PR is opened to base_branch. Set to false to merge directly into base_branch without a PR. (true/false) | no | true |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code changes and investigation happen through headless sessions via run_skill.
- Route to on_failure when a step fails — the downstream skill (e.g., resolve-failures) has diagnostic access that the orchestrator does not. Do not investigate or attempt to fix failures directly.
- SEQUENTIAL EXECUTION: complete full cycle per part before advancing. For each plan_part, run the full cycle (implement → test → merge) before starting the next part. Do NOT batch-implement all parts upfront.
- By default (open_pr=true), a feature branch named from inputs.run_name is created. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
