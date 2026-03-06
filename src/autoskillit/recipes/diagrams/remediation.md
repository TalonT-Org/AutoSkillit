<!-- autoskillit-recipe-hash: sha256:c41ea07102921f0dd906ee1cde7a16d26a1aad8e0c83475eabaa1a6e4c8ef6ae -->
<!-- autoskillit-diagram-format: v3 -->
## remediation
Investigate a problem deeply, plan architectural fix, implement in a feature branch, and open a PR.

**Flow:** 

### Graph
clone  [clone_repo] (retry ×3)
│  ↓ success → fetch_issue
│  ✗ failure → escalate_stop
│
├── [fetch_issue] (retry ×3)  ← only if inputs.issue_url
│       ✗ failure → escalate_stop
│
├── [push_merge_target] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → cleanup_failure
│
investigate  [run_skill] (retry ×3)
│  ↓ success → rectify
│  ✗ failure → cleanup_failure
│
rectify  [run_skill] (retry ×3)
│  ↓ success → review
│  ✗ failure → cleanup_failure
│
┌────┤ FOR EACH:
│  review  [run_skill] (retry ×3)
│  │  ↓ success → dry_walkthrough
│  │  ✗ failure → cleanup_failure
│  │
│  dry_walkthrough  [run_skill] (retry ×3)
│  │  ↓ success → implement
│  │  ✗ failure → rectify ↑
│  │
│  implement  [run_skill] (retry ×∞)
│  │  ↓ success → verify
│  │  ✗ failure → cleanup_failure
│  │  ⌛ context limit → retry_worktree
│  │
│  retry_worktree  [run_skill] (retry ×3)
│  │  ↓ success → verify
│  │  ✗ failure → cleanup_failure
│  │
│  verify  [test_check] (retry ×3)
│  │  ↓ success → audit_impl
│  │  ✗ failure → assess
│  │
│  assess  [run_skill] (retry ×3)
│  │  ↓ success → verify ↑
│  │  ✗ failure → cleanup_failure
│  │
│  ├── [audit_impl] (retry ×3)  ← only if inputs.audit
│  │       ${{ result.verdict }} == GO → merge
│  │       result.error → escalate_stop
│  │       (default) → remediate
│  │       ✗ failure → escalate_stop
│  │
│  remediate  [route] (retry ×3)
│  │  ↓ success → make_plan
│  │
│  make_plan  [run_skill] (retry ×3)
│  │  ↓ success → review ↑
│  │  ✗ failure → cleanup_failure
└────┘
│
merge  [merge_worktree] (retry ×3)
│  result.failed_step == 'test_gate' → assess ↑
│  result.failed_step == 'post_rebase_test_gate' → assess ↑
│  result.failed_step == 'rebase' → assess ↑
│  result.error → cleanup_failure
│  (default) → push
│  ✗ failure → cleanup_failure
│
push  [push_to_remote] (retry ×3)
│  ↓ success → open_pr_step
│  ✗ failure → cleanup_failure
│
├── [open_pr_step] (retry ×3)  ← only if inputs.open_pr
│       ✗ failure → cleanup_failure
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
⏹ done  "Investigation complete. Fix implemented and PR opened."
⏹ escalate_stop  "Human intervention needed. Review the latest output for details."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| topic | Description of the bug, error, or question to investigate | — |
| source_dir | Path to the source repository to clone and work in. Leave empty to auto-detect from git rev-parse --show-toplevel.
 | auto-detect |
| run_name | Name prefix for this pipeline run. Used as the first path component of the feature branch name (e.g. investigate/42 or investigate/20260304) and in the clone directory name.
 | investigate |
| target_dir | Optional additional project directory for context | auto-detect |
| base_branch | Branch to branch off of and PR target | main |
| audit | Run /autoskillit:audit-impl before merge to gate on implementation quality | on |
| review_approach | Run /autoskillit:review-approach before dry walkthrough? (true/false) | off |
| open_pr | Create a feature branch (named from run_name) and open a GitHub PR to merge it into base_branch. The standard workflow — all worktree merges target the feature branch, then a PR is opened to base_branch. Set to false to merge directly into base_branch without a PR. (true/false) | on |
| issue_url | Optional GitHub issue URL (e.g. https://github.com/owner/repo/issues/42). When provided, the issue content is fetched and used to enrich investigation, and the resulting PR will include "Closes #N" to auto-close the issue on merge.
 | auto-detect |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All code changes and investigation happen through headless sessions via run_skill.
- Route to on_failure when a step fails — the downstream skill (e.g., resolve-failures) has diagnostic access that the orchestrator does not. Do not investigate or attempt to fix failures directly.
- SEQUENTIAL EXECUTION: complete full cycle per part before advancing. For each plan_part, run the full cycle (dry_walkthrough → implement → verify → merge) before starting the next part.
- By default (open_pr=true), a feature branch is created with a unique name derived from inputs.run_name and context.issue_number (e.g. investigate/42) or a date suffix (e.g. investigate/20260304) when no issue is available. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
