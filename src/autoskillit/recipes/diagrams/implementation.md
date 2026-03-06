<!-- autoskillit-recipe-hash: sha256:74460d7395b61125566b58fd4345b98622bc55acca39df31689437e69a468f3c -->
<!-- autoskillit-diagram-format: v3 -->
## implementation
Plan, verify, implement, test, and merge a task end-to-end. Optionally decompose a large document into sequenced groups first. Use when user says "run pipeline", "implement task", or "auto implement".

**Flow:** clone > capture_base_sha > set_merge_target > (create_branch?) > (make-groups?) > make-plan > (review-approach?) > dry-walkthrough > implement > test > merge (per group, per plan part) > (audit-impl?) > (open_pr?) > push > cleanup

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
group  [run_skill] (retry ×3)
│  ↓ success → plan
│  ✗ failure → cleanup_failure
│
┌────┤ FOR EACH:
│  plan  [run_skill] (retry ×3)
│  │  ↓ success → review
│  │  ✗ failure → cleanup_failure
│  │
│  review  [run_skill] (retry ×3)
│  │  ↓ success → verify
│  │  ✗ failure → cleanup_failure
│  │
│  verify  [run_skill] (retry ×3)
│  │  ↓ success → implement
│  │  ✗ failure → cleanup_failure
│  │
│  implement  [run_skill] (retry ×∞)
│  │  ↓ success → test
│  │  ✗ failure → cleanup_failure
│  │  ⌛ context limit → retry_worktree
│  │
│  retry_worktree  [run_skill] (retry ×3)
│  │  ↓ success → test
│  │  ✗ failure → cleanup_failure
│  │
│  test  [test_check] (retry ×3)
│  │  ↓ success → merge
│  │  ✗ failure → fix
│  │
│  merge  [merge_worktree] (retry ×3)
│  │  result.failed_step == 'test_gate' → fix
│  │  result.failed_step == 'post_rebase_test_gate' → fix
│  │  result.failed_step == 'rebase' → fix
│  │  result.error → cleanup_failure
│  │  (default) → next_or_done
│  │  ✗ failure → cleanup_failure
│  │
│  push  [push_to_remote] (retry ×3)
│  │  ↓ success → open_pr_step
│  │  ✗ failure → cleanup_failure
│  │
│  fix  [run_skill] (retry ×3)
│  │  ↓ success → test ↑
│  │  ✗ failure → cleanup_failure
│  │  ⌛ context limit → test
│  │
│  next_or_done  [route] (retry ×3)
│  │  ${{ result.next }} == more_parts → verify ↑
│  │  ${{ result.next }} == more_groups → plan ↑
│  │  (default) → audit_impl
│  │
│  ├── [audit_impl] (retry ×3)  ← only if inputs.audit
│  │       ${{ result.verdict }} == GO → push ↑
│  │       result.error → escalate_stop
│  │       (default) → remediate
│  │       ✗ failure → escalate_stop
│  │
│  remediate  [route] (retry ×3)
│  │  ↓ success → plan ↑
└────┘
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
⏹ done  "Implementation pipeline complete. All groups/tasks have been planned, implemented, tested, and merged."
⏹ escalate_stop  "Pipeline failed — human intervention needed. Check the worktree and plan for details."

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| source_dir | Path to the source repository to clone and work in. Leave empty to auto-detect from git rev-parse --show-toplevel.
 | auto-detect |
| run_name | Name prefix for this pipeline run. Used as the first path component of the feature branch name (e.g. impl/124 or impl/20260304) and in the clone directory name.
 | impl |
| base_branch | Branch to merge into (defaults to current branch) | main |
| make_groups | Run /make-groups to decompose source_doc into sequenced implementation groups? (true/false) | off |
| review_approach | Run /review-approach before implementation? (true/false) | off |
| audit | Run /autoskillit:audit-impl once after all groups/parts have been merged, to check overall implementation quality and optionally trigger a remediation round (true/false) | on |
| open_pr | Create a feature branch (named from run_name) and open a GitHub PR to merge it into base_branch. The standard workflow — all worktree merges target the feature branch, then a PR is opened to base_branch. Set to false to merge directly into base_branch without a PR. (true/false) | on |
| issue_url | Optional GitHub issue URL (e.g. https://github.com/owner/repo/issues/42). When provided, the issue content is fetched and used to enrich planning, and the resulting PR will include "Closes #N" to auto-close the issue on merge.
 | auto-detect |

Agent-managed: task, source_doc
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix directly from the orchestrator.
- When make_groups is false, task input is required.
- When make_groups is true, source_doc input is required.
- Process plan parts and groups sequentially. Complete the full cycle (verify → implement → test → merge) for one part before starting the next. Do NOT run verify for all parts upfront.
- By default (open_pr=true), a feature branch is created with a unique name derived from inputs.run_name and context.issue_number (e.g. impl/124) or a date suffix (e.g. impl/20260304) when no issue is available. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
