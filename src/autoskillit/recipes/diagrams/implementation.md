<!-- autoskillit-recipe-hash: sha256:6892239d6d74a72b0acd74b3a7ba266c748bab0ef9744a18ff03c53277a70324 -->
<!-- autoskillit-diagram-format: v2 -->
## implementation
Plan, verify, implement, test, and merge a task end-to-end. Optionally decompose a large document into sequenced groups first. Use when user says "run pipeline", "implement task", or "auto implement".

**Flow:** clone > capture_base_sha > set_merge_target > (create_branch?) > (make-groups?) > make-plan > (review-approach?) > dry-walkthrough > implement > test > merge (per group, per plan part) > (audit-impl?) > (open_pr?) > push > cleanup

### Graph
┌─ clone  [clone_repo]
│  ✓ success  → capture_base_sha
│  ✗ failure  → escalate_stop
│  ↺ ×3  → escalate
│
┌─ capture_base_sha  [run_cmd]
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
│  ✓ success  → group
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ group  [run_skill]
│  ✓ success  → plan
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ plan  [run_skill]
│  ✓ success  → review
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ review  [run_skill]
│  ✓ success  → verify
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ verify  [run_skill]
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
┌─ merge  [merge_worktree]
│  ├─ result.failed_step == 'test_gate'  → fix
│  ├─ result.failed_step == 'rebase'  → fix
│  ├─ result.error  → cleanup_failure
│  ├─ (default)  → next_or_done
│  ↺ ×3  → escalate
│
┌─ push  [push_to_remote]
│  ✓ success  → open_pr_step
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ fix  [run_skill]
│  ✓ success  → test ↑
│  ✗ failure  → cleanup_failure
│  ↺ ×3  → escalate
│
┌─ next_or_done  [route]
│  ├─ ${{ result.next }} == more_parts  → verify ↑
│  ├─ ${{ result.next }} == more_groups  → plan ↑
│  ├─ (default)  → audit_impl
│  ↺ ×3  → escalate
│
│  ⟨skip if inputs.audit is false⟩
┌─ audit_impl  [run_skill]
│  ├─ ${{ result.verdict }} == GO  → push ↑
│  ├─ result.error  → escalate_stop
│  ├─ (default)  → remediate
│  ↺ ×3  → escalate
│
┌─ remediate  [route]
│  ✓ success  → plan ↑
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
⏹ done  "Implementation pipeline complete. All groups/tasks have been planned, implemented, tested, and merged."
⏹ escalate_stop  "Pipeline failed — human intervention needed. Check the worktree and plan for details."

### Ingredients
| Name | Description | Required | Default |
|------|-------------|----------|---------|
| task | Description of what to implement (required when make_groups is false) | no |  |
| source_doc | Path to source document for group decomposition (required when make_groups is true) | no |  |
| source_dir | Path to the source repository to clone and work in. Leave empty to auto-detect from git rev-parse --show-toplevel.
 | no |  |
| run_name | Name prefix for this pipeline run. Used as the first path component of the feature branch name (e.g. impl/124 or impl/20260304) and in the clone directory name.
 | no | impl |
| base_branch | Branch to merge into (defaults to current branch) | no | main |
| make_groups | Run /make-groups to decompose source_doc into sequenced implementation groups? (true/false) | no | false |
| review_approach | Run /review-approach before implementation? (true/false) | no | false |
| audit | Run /autoskillit:audit-impl once after all groups/parts have been merged, to check overall implementation quality and optionally trigger a remediation round (true/false) | no | true |
| open_pr | Create a feature branch (named from run_name) and open a GitHub PR to merge it into base_branch. The standard workflow — all worktree merges target the feature branch, then a PR is opened to base_branch. Set to false to merge directly into base_branch without a PR. (true/false) | no | true |
| issue_url | Optional GitHub issue URL (e.g. https://github.com/owner/repo/issues/42). When provided, the issue content is fetched and used to enrich planning, and the resulting PR will include "Closes #N" to auto-close the issue on merge.
 | no |  |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix directly from the orchestrator.
- When make_groups is false, task input is required.
- When make_groups is true, source_doc input is required.
- Process plan parts and groups sequentially. Complete the full cycle (verify → implement → test → merge) for one part before starting the next. Do NOT run verify for all parts upfront.
- By default (open_pr=true), a feature branch is created with a unique name derived from inputs.run_name and context.issue_number (e.g. impl/124) or a date suffix (e.g. impl/20260304) when no issue is available. All worktree merges target the feature branch (context.merge_target), not base_branch directly. The push step publishes the feature branch, then open_pr_step opens a PR to base_branch. When open_pr=false, merges target base_branch directly and open_pr_step is skipped.
- SOURCE ISOLATION: After clone_repo returns, the source_dir is strictly off-limits. Never run any command in source_dir — no git checkout, git fetch, git reset, git pull, run_cmd, run_skill, or any other operation. All work — skill invocations, git operations, file reads — happens exclusively in the clone (work_dir). source_dir is used ONLY to read the remote URL inside push_to_remote.
