---
name: retry-worktree
description: Continue implementing a plan in an existing git worktree after context exhaustion. Use when a previous implement-worktree session hit context limits. Takes plan path and worktree path as arguments.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: retry-worktree] Resuming worktree implementation...'"
          once: true
---

# Retry Worktree Implementation Skill

Continue implementing a plan in an **existing** git worktree. This skill is used when a previous `/implement-worktree` session hit context limits before completing.

## When to Use

- A previous `/implement-worktree` session exhausted its context
- The worktree already exists with partial implementation
- User provides both the plan path and the existing worktree path

## Arguments

```
/retry-worktree {plan_path} {worktree_path}
```

- **plan_path** — Path to the plan file (relative or absolute)
- **worktree_path** — Path to the existing worktree directory

## Critical Constraints

**NEVER:**
- Create a new worktree — the worktree already exists
- Re-run `uv venv` or `uv pip install` unless the venv is missing/broken
- Re-explore systems that were already explored (skip Step 2 of implement-worktree)
- Implement in the main working directory (always use the worktree)
- Force push or perform destructive git operations
- Consider implementation complete if ANY test fails
- Blame test failures on "pre-existing issues" — ALL tests must pass
- Re-run tests just to see failures — grep the saved output file instead
- Pipe test output through `tail`, `head`, or other truncation commands — `tail -N` buffers the entire stream and produces no output if the process is killed before EOF

**ALWAYS:**
- Use the provided worktree path (do NOT create a new one)
- Start by assessing what has already been implemented
- Continue from where the previous session left off
- Run the project's test suite from the worktree directory
- Rebase onto base branch before completion (ready for squash-and-merge)

## Workflow

### Step 0: Receive and Validate Arguments

Parse two positional arguments from the prompt:
1. **Plan path** — verify the plan file exists and read it
2. **Worktree path** — verify the directory exists, is a git worktree, and has a `.venv`

If the worktree path does not exist:
- Abort with error: "Worktree path does not exist. Use /implement-worktree to create a new worktree."

If the `.venv` is missing or broken:
- Re-create the development environment. Example for Python: `cd {WORKTREE_PATH} && uv venv .venv && uv pip install -e '.[dev]' --python .venv/bin/python`

### Step 1: Assess Current State

1. Read the plan file to understand the full scope
2. Check what has been implemented so far:
   ```bash
   cd {WORKTREE_PATH}
   git log --oneline $(git merge-base HEAD origin/{base_branch})..HEAD
   git diff --stat $(git merge-base HEAD origin/{base_branch})..HEAD
   ```
3. Compare implemented changes against plan phases to determine:
   - Which phases are complete
   - Which phase is partially complete
   - Which phases haven't started

### Step 1.5: Initialize Code Index for Worktree

Set the MCP code-index project path to the worktree so code searches operate on the correct files:

```
mcp__code-index__set_project_path(path="{WORKTREE_PATH}")
```

This must happen before any code-index searches or Explore subagents.

### Step 2: Targeted Exploration (Only If Needed)

Only explore systems related to the **remaining** phases. Do NOT re-explore already-completed work. Use Explore subagents for:
- Files that will be modified in remaining phases
- Test patterns for remaining changes
- Integration points affected by remaining work

### Step 3: Continue Implementation

**All commands must run from `{WORKTREE_PATH}`.** Use absolute paths to avoid CWD drift across Bash tool calls.

For each remaining/incomplete phase:
1. Announce phase objective and files to modify
2. Implement changes
3. Run per-phase verification if plan specifies it
4. Commit per phase if possible
5. Report phase completion

Where practical, delegate test updates to subagents to keep main conversation context lean.

### Step 4: Final Verification

Run the project's code quality checks and test suite from the worktree.

**Example for Python projects:**
```bash
[[ -d "{WORKTREE_PATH}/.venv" ]] || { echo "ERROR: .venv missing in worktree"; exit 1; }
cd {WORKTREE_PATH} && pre-commit run --all-files
cd {WORKTREE_PATH} && .venv/bin/pytest -v
```

If tests fail, fix the issue and re-run.

### Step 5: Rebase for Squash-and-Merge

```bash
git fetch origin
git rebase origin/{base_branch}
```

If conflicts occur, resolve them, `git rebase --continue`, then re-run tests. Report rebase status.

### Step 6: Completion Report

Output to terminal: worktree path, branch name, base branch, status, summary of changes, and next steps (fast-forward merge then clean up).
Change directory before removing worktree to prevent deleting the cwd.
Always confirm the merge went through before removing worktree.
Do not merge until user confirms first!

### Step 6.5: Reset Code Index to Original Project (REQUIRED)

After worktree cleanup, reset the MCP code-index project path back to the original project directory:

```
mcp__code-index__set_project_path(path="{ORIGINAL_PROJECT_PATH}")
```

Failure to do this leaves code-index pointing at a deleted worktree path, breaking all subsequent code searches.

## Error Handling

- **Worktree venv missing** — re-create the development environment. Example for Python: `uv venv .venv && uv pip install -e '.[dev]' --python .venv/bin/python`
- **Phase fails** — report which phase and why, offer to fix/retry, skip (if optional), or abort and clean up
- **Tests fail** — implementation is NOT complete. Fix the issue. If truly unfixable, report to user and ask for guidance. Do NOT proceed or mark as complete.
- **Rebase conflicts** — resolve keeping implementation intent intact, re-run full test suite after
