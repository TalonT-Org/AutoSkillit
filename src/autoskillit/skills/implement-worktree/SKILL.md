---
name: implement-worktree
description: Implement a plan in an isolated git worktree. Use when user says "implement in worktree", "worktree implement", or "isolated implementation". Creates a worktree from current branch, explores affected systems with subagents, then implements phase by phase.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '🌳 [SKILL: implement-worktree] Setting up isolated worktree implementation...'"
          once: true
---

# Implement in Worktree Skill

Implement a provided plan in an isolated git worktree branched from the current branch.

## When to Use

- User says "implement in worktree", "worktree implement", "isolated implementation"
- User provides a plan and wants it executed in a fresh worktree

## Critical Constraints

**NEVER:**
- Implement without first exploring affected systems with subagents
- Implement in the main working directory (always use the worktree)
- Force push or perform destructive git operations
- Consider implementation complete if ANY test fails
- Blame test failures on "pre-existing issues" — ALL tests must pass
- Re-run tests just to see failures — grep the saved output file instead

**ALWAYS:**
- Create a new worktree from the current branch
- Use subagents to deeply understand affected systems BEFORE implementing
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Implement one phase at a time
- Run the project's test suite after implementation
- Rebase onto base branch before completion (ready for squash-and-merge)

## Context Limit Behavior

If this skill hits the Claude context limit mid-execution, the headless session
terminates with `needs_retry=true` in the tool response. The worktree remains
intact on disk with all commits made up to that point.

The orchestrator **must not** retry this skill when `needs_retry=true`. Retrying
creates a brand-new timestamped worktree, discarding all partial progress.

Correct orchestration on `needs_retry=true`:
- Route immediately to `/autoskillit:retry-worktree` (via `retry.on_exhausted`)
- Pass `worktree_path` from `context.worktree_path` (captured from this step's output)
- Use `max_attempts: 0` on this step's `retry` block to ensure immediate escalation

## Workflow

### Step 0: Validate Prerequisites

1. Verify plan exists (file path or pasted content)
2. **Check for dry-walkthrough verification:** Read the first line of the plan file. If it does not contain exactly `Dry-walkthrough verified = TRUE`:
   - Display warning: "⚠️ WARNING: This plan has NOT been validated with a dry-walkthrough. Implementation may encounter issues that could have been caught beforehand."
   - Use `AskUserQuestion` to prompt: "Do you want to continue without dry-walkthrough validation?"
   - If user declines, abort and suggest running `/autoskillit:dry-walkthrough` first
3. Check `git status --porcelain` — if dirty, warn user
4. Parse plan: phases, files per phase, verification commands

### Step 1: Create Git Worktree

```bash
WORKTREE_NAME="impl-{plan_name}-$(date +%Y%m%d-%H%M%S)"
WORKTREE_PATH="../worktrees/${WORKTREE_NAME}"
git worktree add -b "${WORKTREE_NAME}" "${WORKTREE_PATH}"
```

### Step 1.5: Initialize Code Index for Original Project

Set the MCP code-index project path to the **original project directory** so Explore subagents can use code search tools:

```
mcp__code-index__set_project_path(path="{ORIGINAL_PROJECT_PATH}")
```

This must happen before Step 2 or any code-index search tools will fail with "Project path not set."

### Step 2: Deep System Understanding (Subagents)

Before implementing ANY code, launch parallel Explore subagents to understand affected systems:
- **Affected files** — current implementation, dependencies, consumers
- **Test coverage** — existing tests, patterns, fixtures for affected code
- **Integration points** — entry/exit points, contracts that must be maintained
- **Data flow** — state management, source of truth

### Step 3: Set Up Worktree Environment

Set up the project's development environment in the worktree. Use the project's configured `worktree_setup.command` from `.autoskillit/config.yaml` if available. If not configured, check for a Taskfile with `install-worktree` task, or detect the project type and run appropriate setup.

```bash
cd "${WORKTREE_PATH}"
# If worktree_setup.command is configured, run it. Otherwise:
task install-worktree   # or equivalent for the project type
```

**Why isolated env matters:** Installing packages without isolation overwrites the global state. When the worktree is deleted, CLI commands break with import errors.

**All commands in Steps 4–6 must run from `${WORKTREE_PATH}`.** Use absolute paths to avoid CWD drift across Bash tool calls.

### Step 3.5: Re-point Code Index to Worktree (REQUIRED)

**CRITICAL:** After setting up the worktree environment, you **MUST** update the MCP code-index project path to the worktree. This ensures all subsequent code searches operate on the worktree's files (which will diverge from the original as implementation proceeds):

```
mcp__code-index__set_project_path(path="${WORKTREE_PATH}")
```

**Failure to do this means code-index searches will return results from the original project, not your worktree — leading to confusion and incorrect file reads.**

### Step 4: Implement Phase by Phase

For each phase:
1. Announce phase objective and files to modify
2. Implement changes guided by understanding from Step 2
3. Run per-phase verification if plan specifies it
4. Commit per phase if possible
5. Report phase completion

Where practical, delegate test updates to subagents to keep main conversation context lean.

### Step 5: Final Verification

Run the project's code quality checks and test suite from the worktree.

```bash
cd "${WORKTREE_PATH}" && pre-commit run --all-files
cd "${WORKTREE_PATH}" && task test-all
```

If tests fail, fix the issue and re-run.

### Step 6: Rebase for Squash-and-Merge

```bash
git fetch origin
git rebase origin/{base_branch}
```

If conflicts occur, resolve them, `git rebase --continue`, then re-run tests. Report rebase status.

### Step 7: Completion Report

Output to terminal: worktree path, branch name, base branch, status, summary of changes, and next steps (fast-forward merge then clean up).
Change directory before removing worktree to prevent deleting the cwd.
Always confirm the merge went through before removing work tree.
Do not merge until user confirms first!

### Step 7.5: Reset Code Index to Original Project (REQUIRED)

**CRITICAL:** After worktree cleanup, you **MUST** reset the MCP code-index project path back to the original project directory:

```
mcp__code-index__set_project_path(path="{ORIGINAL_PROJECT_PATH}")
```

**Failure to do this leaves code-index pointing at a deleted worktree path, breaking all subsequent code searches in any session.**

## Error Handling

- **Worktree creation fails** — check `git worktree list`, suggest `git worktree prune`
- **Phase fails** — report which phase and why, offer to fix/retry, skip (if optional), or abort and clean up
- **Tests fail** — implementation is NOT complete. Fix the issue. If truly unfixable, report to user and ask for guidance. Do NOT proceed or mark as complete.
- **Rebase conflicts** — resolve keeping implementation intent intact, re-run full test suite after
