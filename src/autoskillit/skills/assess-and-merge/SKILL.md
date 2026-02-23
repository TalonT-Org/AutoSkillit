---
name: assess-and-merge
description: Fix test failures in a worktree and merge when green. Use when tests fail after implementation. Takes worktree path, plan path, and base branch as arguments.
---

# Assess and Merge Skill

Fix test failures in a worktree implemented by `/autoskillit:implement-worktree-no-merge`, then merge when tests pass.

## When to Use

- MCP orchestrator calls this via `run_skill` after `test_check` returns FAIL
- Takes three positional arguments: `{worktree_path} {plan_path} {base_branch}`

## Critical Constraints

**NEVER:**
- Merge if ANY test fails
- Make changes unrelated to fixing test failures
- Exceed 3 fix-and-retest iterations
- Delete the worktree if tests still fail after max attempts
- Modify the plan file
- Reimplement rebase/merge/cleanup logic — use the `merge_worktree` MCP tool

**ALWAYS:**
- Read the plan first to understand implementation intent
- Commit each fix iteration separately with descriptive messages
- Report iteration count and what was fixed
- Leave worktree intact on failure for manual inspection

## Workflow

### Step 0: Validate Arguments

1. Parse three positional args: `{worktree_path}`, `{plan_path}`, `{base_branch}`
2. Verify worktree exists and is a valid git worktree
3. Verify plan file exists and is readable
4. Check for development environment in worktree, recreate if missing.
   Use the project's configured `worktree_setup.command`, or:
   ```bash
   cd "${worktree_path}" && task install-worktree
   ```

### Step 1: Understand Context

1. Read the plan file to understand what was implemented and why
2. Run `git log --oneline $(git merge-base HEAD origin/{base_branch})..HEAD` to see implementation commits
3. Run `git diff --stat $(git merge-base HEAD origin/{base_branch})..HEAD` to see scope of changes

### Step 2: Run Tests

1. Run the project's test suite from the worktree: `cd {worktree_path} && task test-all`
2. If tests pass: go to Step 4 (Merge)
3. If tests fail: go to Step 3 (Fix Loop)

### Step 3: Fix Loop (max 3 iterations)

1. Analyze test failures against the plan to understand root cause
2. Apply targeted fixes — only change what's needed to make tests pass
3. Commit each fix with a descriptive message: `fix: {what was wrong and why}`
4. Re-run the project's test suite: `cd {worktree_path} && task test-all`
5. If green: go to Step 4
6. If red and iterations < 3: repeat Step 3
7. If red and iterations >= 3: go to Step 5 (Report Failure)

### Step 4: Merge via `merge_worktree` MCP tool

1. Call `merge_worktree(worktree_path, base_branch)` — merge_worktree always runs its own test gate
2. If `merge_worktree` returns error: report the `failed_step` and `state` from the response
3. If `merge_worktree` returns success: report merged branch and summary

### Step 5: Report Failure

Output to terminal:
- Total fix iterations attempted
- Remaining test failures (summary)
- Worktree path (left intact for manual inspection)
- Suggestion: review failures manually or run `/autoskillit:rectify` for deeper analysis
