---
name: implement-worktree-no-merge
description: Implement a plan in an isolated git worktree without merging, testing, or cleaning up. For MCP orchestration use — the orchestrator handles testing and merging separately.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '🌳 [SKILL: implement-worktree-no-merge] Implementing in isolated worktree (no merge)...'"
          once: true
---

# Implement in Worktree (No Merge) Skill

Implement a provided plan in an isolated git worktree branched from the current branch.
The worktree is left intact for the orchestrator to test and merge separately.

## When to Use

- MCP orchestrator calls this via `run_skill_retry`
- Orchestrator wants to control test/merge gates independently

## Critical Constraints

**NEVER:**
- Implement without first exploring affected systems with subagents
- Implement in the main working directory (always use the worktree)
- Force push or perform destructive git operations
- Merge the worktree branch into any branch
- Delete or remove the worktree
- Run the full test suite (the orchestrator handles testing)
- Rebase onto the base branch
- Clean up the worktree environment
- Re-run tests just to see failures — grep the saved output file instead
- Pipe test output through `tail`, `head`, or other truncation commands

**ALWAYS:**
- Create a new worktree from the current branch
- Use subagents to deeply understand affected systems BEFORE implementing
- Implement one phase at a time
- Commit per phase with descriptive messages
- Leave the worktree intact when done

## Workflow

### Step 0: Validate Prerequisites

1. Verify plan exists (file path or pasted content)
2. **Check for dry-walkthrough verification:** Read the first line of the plan file. If it does not contain exactly `Dry-walkthrough verified = TRUE`:
   - Display warning: "WARNING: This plan has NOT been validated with a dry-walkthrough. Implementation may encounter issues that could have been caught beforehand."
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

**All commands in Steps 4–5 must run from `${WORKTREE_PATH}`.** Use absolute paths to avoid CWD drift across Bash tool calls.

### Step 3.5: Re-point Code Index to Worktree (REQUIRED)

**CRITICAL:** After setting up the worktree environment, you **MUST** update the MCP code-index project path to the worktree:

```
mcp__code-index__set_project_path(path="${WORKTREE_PATH}")
```

**Failure to do this means code-index searches will return results from the original project, not your worktree.**

### Step 4: Implement Phase by Phase

For each phase:
1. Announce phase objective and files to modify
2. Implement changes guided by understanding from Step 2
3. Run per-phase verification if plan specifies it
4. Commit per phase with descriptive messages
5. Report phase completion

Where practical, delegate test updates to subagents to keep main conversation context lean.

### Step 5: Run Pre-commit Checks

```bash
cd "${WORKTREE_PATH}" && pre-commit run --all-files
```

Fix any formatting or linting issues. Do NOT run the full test suite.

### Step 6: Handoff Report

Output to terminal:
- **Worktree path:** `${WORKTREE_PATH}`
- **Branch name:** `${WORKTREE_NAME}`
- **Base branch:** the branch the worktree was created from
- **Summary:** list of implemented phases and key changes

Explicitly state: "Worktree left intact for orchestrator to test and merge."

### Step 6.5: Reset Code Index to Original Project (REQUIRED)

**CRITICAL:** After completion, you **MUST** reset the MCP code-index project path back to the original project directory:

```
mcp__code-index__set_project_path(path="{ORIGINAL_PROJECT_PATH}")
```

## Error Handling

- **Worktree creation fails** — check `git worktree list`, suggest `git worktree prune`
- **Phase fails** — report which phase and why, offer to fix/retry, skip (if optional), or abort. Do NOT clean up the worktree.
- **Pre-commit fails** — fix formatting/linting issues and re-commit
