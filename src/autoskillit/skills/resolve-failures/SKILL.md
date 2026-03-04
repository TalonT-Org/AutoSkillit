---
name: resolve-failures
description: Fix test failures in a worktree without merging. Leaves worktree green and unmerged for the orchestrator's merge gate. Use when tests fail after implementation. Takes worktree path, plan path, and base branch as positional arguments.
---

# Resolve Failures Skill

Fix test failures in a worktree implemented by `/autoskillit:implement-worktree-no-merge`, leaving the worktree green and unmerged for the orchestrator's merge gate.

## When to Use

- MCP orchestrator calls this via `run_skill` after `test_check` returns FAIL
- Takes three positional arguments: `{worktree_path} {plan_path} {base_branch}`
- Remediates test failures only — the orchestrator is responsible for calling `merge_worktree` after verify passes.

## Critical Constraints

**NEVER:**
- Merge if ANY test fails
- Merge via `merge_worktree` or any other mechanism
- Call `merge_worktree` MCP tool
- Make changes unrelated to fixing test failures
- Exceed 3 fix-and-retest iterations
- Delete the worktree if tests still fail after max attempts
- Modify the plan file
- Create files outside `temp/resolve-failures/` directory

**ALWAYS:**
- Read the plan first to understand implementation intent
- Commit each fix iteration separately with descriptive messages
- Report iteration count and what was fixed
- Leave worktree intact on failure for manual inspection

## Workflow

### Step 0: Validate Arguments
1. Parse three positional args using **path detection**: scan all tokens after
   the skill name for those starting with `/`, `./`, or `.autoskillit/`. The
   first path-like token is `worktree_path`; the second is `plan_path`. The
   `base_branch` is the remaining non-path token. Ignore any non-path tokens
   that appear before the path arguments. If fewer than two path-like tokens
   are found, abort with a clear error and the correct format:
   `/autoskillit:resolve-failures <worktree_path> <plan_path> <base_branch>`
2. Verify worktree exists and is a valid git worktree
3. Verify plan file exists and is readable
4. Check for development environment in worktree, recreate if missing. Use the project's configured `worktree_setup.command`, or: `cd "${worktree_path}" && task install-worktree`

### Step 1: Understand Context
1. Read the plan file to understand what was implemented and why
2. Run `git log --oneline $(git merge-base HEAD origin/{base_branch})..HEAD`
3. Run `git diff --stat $(git merge-base HEAD origin/{base_branch})..HEAD`

### Step 2: Run Tests
1. Run `cd {worktree_path} && task test-all`
2. If tests pass: go to Step 4 (Report Success)
3. If tests fail: go to Step 3 (Fix Loop)

### Step 3: Fix Loop (max 3 iterations)
1. Analyze test failures against the plan to understand root cause
2. Apply targeted fixes
3. If the project has pre-commit hooks, run `pre-commit run --all-files` and
   stage any auto-fixed files before committing. Commit each fix: `fix: {what was wrong and why}`
4. Re-run: `cd {worktree_path} && task test-all`
5. Green → Step 4; Red and < 3 iterations → repeat; Red and >= 3 → Step 5

### Step 4: Report Success

Tests are green. Report success and exit — do NOT merge.

Output to terminal:
- Total fix iterations performed (may be 0 if tests were already passing on re-run)
- Summary of what was fixed (or "no changes needed")
- Worktree path (left intact for orchestrator's merge gate)

Return control to the orchestrator. The `merge_worktree` MCP tool will be
called by the recipe pipeline, not by this skill.

### Step 5: Report Failure
- Total fix iterations attempted
- Remaining test failures (summary)
- Worktree path (left intact for manual inspection)
- Suggestion: review failures manually or run `/autoskillit:rectify`
