<!-- autoskillit-recipe-hash: sha256:436fed625e095eaf1c010df865042b0bf2085c24defdb4e98899a7197c77da52 -->
<!-- autoskillit-diagram-format: v2 -->
## audit-and-fix
Audit codebase, investigate findings, plan fixes, implement in a feature branch, and open a PR.

**Flow:** 

### Graph
┌─ clone  [clone_repo]
│  ✓ success  → set_merge_target
│  ✗ failure  → escalate_stop
│  ↺ ×3  → escalate
│
┌─ set_merge_target  [run_cmd]
│  ✓ success  → fetch_issue
│  ✗ failure  → escalate_stop
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.issue_url is false⟩
┌─ fetch_issue  [fetch_github_issue]
│  ✓ success  → create_branch
│  ✗ failure  → escalate_stop
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.open_pr is false⟩
┌─ create_branch  [run_cmd]
│  ✓ success  → push_merge_target
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.open_pr is false⟩
┌─ push_merge_target  [push_to_remote]
│  ✓ success  → audit
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ audit  [run_skill]
│  ✓ success  → investigate
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ investigate  [run_skill]
│  ✓ success  → plan
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ plan  [run_skill]
│  ✓ success  → implement
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ implement  [run_skill]
│  ✓ success  → test
│  ✗ failure  → cleanup_failure
│
┌─ retry_worktree  [run_skill]
│  ✓ success  → test
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → cleanup_failure
│
┌─ test  [test_check]
│  ✓ success  → merge
│  ✗ failure  → fix
│  ↺ ×3  → escalate
│
┌─ fix  [run_skill]
│  ✓ success  → test ↑
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ merge  [merge_worktree]
│  ✓ success  → push
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ push  [push_to_remote]
│  ✓ success  → open_pr_step
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.open_pr is false⟩
┌─ open_pr_step  [run_skill]
│  ✓ success  → cleanup_success
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ cleanup_success  [remove_clone]
│  ✓ success  → done
│  ✗ failure  → done
│  ↺ ×3  → escalate
│
┌─ cleanup_failure  [remove_clone]
│  ✓ success  → escalate_stop
│  ✗ failure  → escalate_stop
│  ↺ ×3  → escalate
│
───────────────────────────────────────
⏹ done  "Audit findings addressed. Changes merged via PR."
⏹ escalate_stop  "Human intervention needed."

### Ingredients
| Name | Description | Required | Default |
|------|-------------|----------|---------|
| source_dir | Path to the source repository to clone and work in. Leave empty to auto-detect from git rev-parse --show-toplevel.
 | no |  |
| run_name | Name prefix for this pipeline run. Used as the first path component of the feature branch name (e.g. audit-fix/124 or audit-fix/20260304) and in the clone directory name.
 | no | audit-fix |
| base_branch | Branch to branch off of and PR target | no | main |
| audit_type | Type of audit to run (arch, tests, cohesion, defense-standards) | no | arch |
| open_pr | Create a feature branch (named from run_name) and open a GitHub PR to merge it into base_branch. The standard workflow — all worktree merges target the feature branch, then a PR is opened to base_branch. Set to false to merge directly into base_branch without a PR. (true/false) | no | true |
| issue_url | Optional GitHub issue URL (e.g. https://github.com/owner/repo/issues/42). When provided, the issue content is fetched for additional audit context, and the resulting PR will include "Closes #N" to auto-close the issue on merge.
 | no |  |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code changes and investigation happen through headless sessions via run_skill.
- Route to on_failure when a step fails — the downstream skill (e.g., resolve-failures) has diagnostic access that the orchestrator does not. Do not investigate or attempt to fix failures directly.
- SEQUENTIAL EXECUTION: complete full cycle per part before advancing. For each plan_part, run the full cycle (implement → test → merge) before starting the next part. Do NOT batch-implement all parts upfront.
- By default (open_pr=true), a feature branch is created with a unique name derived from inputs.run_name and context.issue_number (e.g. audit-fix/124) or a date suffix (e.g. audit-fix/20260304) when no issue is available. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
