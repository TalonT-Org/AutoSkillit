<!-- autoskillit-recipe-hash: sha256:5b8623acc898782c9ef4dc83d2aae9bbc3790ae1155f51aec51aa8262fae98ed -->
<!-- autoskillit-diagram-format: v3 -->
## audit-and-fix
Audit codebase, investigate findings, plan fixes, implement in a feature branch, and open a PR.

**Flow:** 

### Graph
clone  [clone_repo] (retry ×3)
│  ↓ success → push_merge_target
│  ✗ failure → escalate_stop
│
├── [push_merge_target] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → cleanup_failure
│
audit  [run_skill] (retry ×3)
│  ↓ success → investigate
│  ✗ failure → cleanup_failure
│
investigate  [run_skill] (retry ×3)
│  ↓ success → plan
│  ✗ failure → cleanup_failure
│
plan  [run_skill] (retry ×3)
│  ↓ success → implement
│  ✗ failure → cleanup_failure
│
implement  [run_skill] (retry ×∞)
│  ↓ success → test
│  ✗ failure → cleanup_failure
│  ⌛ context limit → retry_worktree
│
retry_worktree  [run_skill] (retry ×3)
│  ↓ success → test
│  ✗ failure → cleanup_failure
│
test  [test_check] (retry ×3)
│  ↓ success → merge
│  ✗ failure → fix
│
fix  [run_skill] (retry ×3)
│  ↓ success → test ↑
│  ✗ failure → cleanup_failure
│  ⌛ context limit → test
│
merge  [merge_worktree] (retry ×3)
│  ↓ success → push
│  ✗ failure → cleanup_failure
│
push  [push_to_remote] (retry ×3)
│  ↓ success → open_pr_step
│  ✗ failure → cleanup_failure
│
├── [open_pr_step] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → cleanup_failure
│
┌────┤ FOR EACH:
│  ├── [review_pr] (retry ×3)  ← only if inputs.open_pr
│  │       ✗ failure → resolve_review
│  │
│  resolve_review  [run_skill] (retry ×2)
│  │  ↓ success → re_push_review
│  │  ✗ failure → cleanup_failure
│  │
│  re_push_review  [push_to_remote] (retry ×3)
│  │  ↓ success → review_pr ↑
│  │  ✗ failure → cleanup_failure
└────┘
│
├── [ci_watch] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → resolve_ci
│
resolve_ci  [run_skill] (retry ×2)
│  ↓ success → re_push
│  ✗ failure → cleanup_failure
│
re_push  [push_to_remote] (retry ×3)
│  ↓ success → ci_watch ↑
│  ✗ failure → cleanup_failure
│
cleanup_success  [remove_clone] (retry ×3)
│  ↓ success → done
│  ✗ failure → done
│
cleanup_failure  [remove_clone] (retry ×3)
│  ↓ success → escalate_stop
│  ✗ failure → escalate_stop
│
─────────────────────────────────────
⏹ done  "Audit findings addressed. Changes merged via PR."
⏹ escalate_stop  "Human intervention needed."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| source_dir | Path to the source repository to clone and work in. Leave empty to auto-detect from git rev-parse --show-toplevel.
 | auto-detect |
| run_name | Name prefix for this pipeline run. Used as the first path component of the feature branch name (e.g. audit-fix/124 or audit-fix/20260304) and in the clone directory name.
 | audit-fix |
| base_branch | Branch to branch off of and PR target | main |
| audit_type | Type of audit to run (arch, tests, cohesion, defense-standards) | arch |
| open_pr | Create a feature branch (named from run_name) and open a GitHub PR to merge it into base_branch. The standard workflow — all worktree merges target the feature branch, then a PR is opened to base_branch. Set to false to merge directly into base_branch without a PR. (true/false) | on |
| issue_url | Optional GitHub issue URL (e.g. https://github.com/owner/repo/issues/42). When provided, the issue content is fetched for additional audit context, and the resulting PR will include "Closes #N" to auto-close the issue on merge.
 | auto-detect |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code changes and investigation happen through headless sessions via run_skill.
- Route to on_failure when a step fails — the downstream skill (e.g., resolve-failures) has diagnostic access that the orchestrator does not. Do not investigate or attempt to fix failures directly.
- SEQUENTIAL EXECUTION: complete full cycle per part before advancing. For each plan_part, run the full cycle (implement → test → merge) before starting the next part. Do NOT batch-implement all parts upfront.
- By default (open_pr=true), a feature branch is created with a unique name derived from inputs.run_name and context.issue_number (e.g. audit-fix/124) or a date suffix (e.g. audit-fix/20260304) when no issue is available. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
