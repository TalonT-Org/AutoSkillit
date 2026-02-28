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
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Implement one phase at a time
- Commit per phase with descriptive messages
- Leave the worktree intact when done

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
   - Display warning: "WARNING: This plan has NOT been validated with a dry-walkthrough. Implementation may encounter issues that could have been caught beforehand."
   - Use `AskUserQuestion` to prompt: "Do you want to continue without dry-walkthrough validation?"
   - If user declines, abort and suggest running `/autoskillit:dry-walkthrough` first
3. Check `git status --porcelain` — if dirty, warn user
4. Parse plan: phases, files per phase, verification commands
5. **Multi-Part Plan Detection:** Examine the plan filename. If it contains `_part_` (e.g., `_part_a`, `_part_b`, `_part_1`):
   - Extract the part identifier (A, B, C… or number) from the suffix.
   - **SCOPE FENCE — MANDATORY:** Before any exploration or implementation begins, output the following constraint:
     > "🚧 SCOPE FENCE ACTIVE: I am implementing PART {X} ONLY. I MUST NOT open, read, or execute any other part files, regardless of what I encounter in temp/ or any other directory. Sibling part files are out of scope for this entire session."
   - When launching subagents in Step 2, include this fence instruction explicitly in each subagent prompt so that the subagents do not open, read, or reference sibling part files.

### Step 1: Create Git Worktree

```bash
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
WORKTREE_NAME="impl-{plan_name}-$(date +%Y%m%d-%H%M%S)"
WORKTREE_PATH="../worktrees/${WORKTREE_NAME}"
git worktree add -b "${WORKTREE_NAME}" "${WORKTREE_PATH}"

# Record the base branch in two ways for reliable discovery by retry-worktree:
# 1) Write an explicit file store (works with any Git version, works offline)
mkdir -p ".autoskillit/temp/worktrees/${WORKTREE_NAME}"
echo "${CURRENT_BRANCH}" > ".autoskillit/temp/worktrees/${WORKTREE_NAME}/base-branch"
# 2) Set git upstream tracking (requires remote tracking ref in local fetch cache)
git fetch origin "${CURRENT_BRANCH}" 2>/dev/null || true
git -C "${WORKTREE_PATH}" branch --set-upstream-to="origin/${CURRENT_BRANCH}" "${WORKTREE_NAME}" 2>/dev/null || true
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

Then emit these structured output tokens on their own lines so recipe capture blocks can extract them:

```
worktree_path=${WORKTREE_PATH}
branch_name=${WORKTREE_NAME}
```

### Step 6.5: Reset Code Index to Original Project (REQUIRED)

**CRITICAL:** After completion, you **MUST** reset the MCP code-index project path back to the original project directory:

```
mcp__code-index__set_project_path(path="{ORIGINAL_PROJECT_PATH}")
```

## Error Handling

- **Worktree creation fails** — check `git worktree list`, suggest `git worktree prune`
- **Phase fails** — report which phase and why, offer to fix/retry, skip (if optional), or abort. Do NOT clean up the worktree.
- **Pre-commit fails** — fix formatting/linting issues and re-commit
