<!-- autoskillit-recipe-hash: sha256:d2135cb4749920cd0664758c513b91a799a44f63b8a00aac06fd66727ac17ab4 -->
<!-- autoskillit-diagram-format: v4 -->
## implementation-groups
Decompose a source document into sequenced implementation groups, then plan, verify, implement, test, and merge each group end-to-end. Use when you have a large document or roadmap to implement via make-groups.

**Flow:** clone > capture_base_sha > set_merge_target > (create_branch?) > make-groups > make-plan > (review-approach?) > dry-walkthrough > implement > test > merge (per group, per plan part) > (audit-impl?) > (open_pr?) > push > cleanup

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
┌────┤ FOR EACH GROUP / PLAN PART:
│    │
│    plan (retry ×3) ─── review (retry ×3) ─── verify (retry ×3) ─── implement (retry ×∞) ─── retry_worktree (retry ×3) ─── test (retry ×3) ─── merge (retry ×3) ─── push (retry ×3) ─── fix (retry ×3) ↑ ─── next_or_done (retry ×3)
│     │
│     ✗ failure → cleanup_failure
│                         │
│                         ✗ failure → cleanup_failure
│                                               │
│                                               ✗ failure → cleanup_failure
│                                                                     │
│                                                                     ✗ failure → cleanup_failure
│                                                                     ⌛ context limit → retry_worktree
│                                                                                              │
│                                                                                              ✗ failure → cleanup_failure
│                                                                                                                            │
│                                                                                                                            ✗ failure → fix
│                                                                                                                                                │
│                                                                                                                                                ✗ failure → cleanup_failure
│                                                                                                                                                result.failed_step == 'test_gate' → fix
│                                                                                                                                                result.failed_step == 'post_rebase_test_gate' → fix
│                                                                                                                                                result.failed_step == 'rebase' → fix
│                                                                                                                                                result.error → cleanup_failure
│                                                                                                                                                (default) → next_or_done
│                                                                                                                                                                     │
│                                                                                                                                                                     ✗ failure → cleanup_failure
│                                                                                                                                                                                         │
│                                                                                                                                                                                         ✗ failure → cleanup_failure
│                                                                                                                                                                                         ⌛ context limit → test
│                                                                                                                                                                                                              │
│                                                                                                                                                                                                              ${{ result.next }} == more_parts → verify ↑
│                                                                                                                                                                                                              ${{ result.next }} == more_groups → plan ↑
│                                                                                                                                                                                                              (default) → audit_impl
│
└────┘
│
├── [audit_impl] (retry ×3)  ← only if inputs.audit
│       ${{ result.verdict }} == GO → push ↑
│       result.error → escalate_stop
│       (default) → remediate
│       ✗ failure → escalate_stop
│
remediate  [route] (retry ×3)
│  ↓ success → plan ↑
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
| source_doc | Path to source document for group decomposition | — |
| source_dir | Path to the source repository to clone and work in. Leave empty to auto-detect from git rev-parse --show-toplevel.
 | auto-detect |
| run_name | Name prefix for this pipeline run. Used as the first path component of the feature branch name (e.g. impl/124 or impl/20260304) and in the clone directory name.
 | impl |
| base_branch | Branch to merge into (defaults to current branch) | main |
| review_approach | Run /review-approach before implementation? (true/false) | off |
| audit | Run /autoskillit:audit-impl once after all groups/parts have been merged, to check overall implementation quality and optionally trigger a remediation round (true/false) | on |
| open_pr | Create a feature branch (named from run_name) and open a GitHub PR to merge it into base_branch. The standard workflow — all worktree merges target the feature branch, then a PR is opened to base_branch. Set to false to merge directly into base_branch without a PR. (true/false) | on |
| issue_url | Optional GitHub issue URL (e.g. https://github.com/owner/repo/issues/42). When provided, the issue content is fetched and used to enrich planning, and the resulting PR will include "Closes #N" to auto-close the issue on merge.
 | auto-detect |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix directly from the orchestrator.
- source_doc is required — it provides the work via group decomposition.
- Process plan parts and groups sequentially. Complete the full cycle (verify → implement → test → merge) for one part before starting the next. Do NOT run verify for all parts upfront.
- By default (open_pr=true), a feature branch is created with a unique name derived from inputs.run_name and context.issue_number (e.g. impl/124) or a date suffix (e.g. impl/20260304) when no issue is available. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
