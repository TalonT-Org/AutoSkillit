---
name: retry-worktree
uses_capabilities: [agent_model, cross_skill_ref]
description: Worktree retry executor. ALWAYS invoke this skill when instructed to continue or retry an implementation in an existing worktree. Do not resume editing files directly — use this skill first to load the retry workflow.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: retry-worktree] Resuming worktree implementation...'"
          once: true
---

# Retry Worktree Implementation Skill

Continue implementing a plan in an **existing** git worktree. This skill is used when a previous `/autoskillit:implement-worktree` session hit context limits before completing.

## When to Use

- A previous `/autoskillit:implement-worktree` session exhausted its context
- The worktree already exists with partial implementation
- User provides both the plan path and the existing worktree path

## Arguments

```
/autoskillit:retry-worktree {plan_path} {worktree_path}
```

- **plan_path** — Path to the plan file (relative or absolute)
- **worktree_path** — Absolute path to the existing worktree directory

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Create a new worktree — the worktree already exists
- Re-run worktree setup (e.g. `task install-worktree`) unless the environment is missing/broken
- Re-explore systems that were already explored (skip Step 2 of implement-worktree)
- Implement in the main working directory (always use the worktree)
- Force push or perform destructive git operations
- Consider implementation complete if ANY test fails
- Blame test failures on "pre-existing issues" — ALL tests must pass
- Re-run tests just to see failures — grep the saved output file instead
- Pipe test output through `tail`, `head`, or other truncation commands — `tail -N` buffers the entire stream and produces no output if the process is killed before EOF
- Default to `main` as the base branch — always discover it from git's upstream structure or the explicit base-branch store file
- Run subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Use the provided worktree path (do NOT create a new one)
- Spawn all subagents via `Agent(model="sonnet")`
- Start by assessing what has already been implemented
- Continue from where the previous session left off
- Run the project's test suite from the worktree directory
- Rebase onto base branch before completion (ready for squash-and-merge)
- **Read before editing**: Before issuing an `Edit` call on any file, ensure you have issued a `Read` on that file earlier in this session. Claude Code rejects `Edit` on unread files — the retry wastes a full API turn at current context size. If you are uncertain whether a file was read, issue a targeted `Read` (offset + limit to the region you plan to edit) rather than risk an error. **Note:** Reads performed by subagents (Task/Agent) do NOT satisfy this requirement — they run in a child session whose reads are invisible to the parent. If a file was only read inside a subagent, you must Read it again in this main session before calling Edit.
- **Read files fully**: When reading a file to understand it in full, read it in a single call without a `limit` parameter. Do not paginate files with sequential offset reads — read once completely. Use `limit`/`offset` only for targeted section reads of files you have already read in full.
- Issue all Task calls in a single message to maximize parallelism

## Context Limit Behavior

When context is exhausted mid-execution, implementation changes may be on disk but
not yet committed. The recipe routes to `on_context_limit`, preserving the worktree.

**Before emitting structured output tokens:**
1. Run `git -C {WORKTREE_PATH} status --porcelain`
2. If any files are dirty: `git -C {WORKTREE_PATH} add -- <files you modified> && git -C {WORKTREE_PATH} commit -m "chore: commit pending changes before context limit"` — do NOT use `--amend`.
3. Only then emit the `worktree_path`, `branch_name`, and `phases_implemented` tokens

This ensures that all implementation progress is committed and the downstream
merge gate receives a clean worktree when the recipe resumes.

## Workflow

### Step 0: Receive and Validate Arguments

Parse two positional arguments from the prompt:
1. **Plan path** — verify the plan file exists and read it
2. **Worktree path** — verify the directory exists and is a git worktree. Check that the development environment is set up (e.g. `.venv` exists for Python projects)

**Path Detection:** Use path detection to locate both arguments. Scan all
tokens after the skill name for those starting with `/`, `./`, `{{AUTOSKILLIT_TEMP}}/`, or
`.autoskillit/`. The first such token is `plan_path`; the second is
`worktree_path`. Ignore any non-path tokens that appear before them (e.g.,
extra descriptive text like "use this plan" or "from worktree"). If fewer than
two path-like tokens are found, abort with a clear error listing what was
missing and the correct format:
`/autoskillit:retry-worktree <plan_path> <worktree_path>`

If the worktree path does not exist:
- Abort with error: "Worktree path does not exist. Use /autoskillit:implement-worktree to create a new worktree."

If the environment is missing or broken:
- Re-create the development environment using the project's configured `worktree_setup.command`, or: `cd {WORKTREE_PATH} && task install-worktree`

**If `worktree_path` argument is empty or missing:**
Abort with error: "Worktree path argument is empty. The implement step must have
captured worktree_path before context exhaustion. Check that the implement step's
capture block ran before context was exhausted."

This is not a fallback — if worktree_path is empty, the recipe must be inspected
to determine why the capture did not complete. A common cause is context exhaustion
occurring before the skill reached its Step 6 handoff report.

**Session timestamp:** Compute `SESSION_TS` as the current UTC timestamp in `YYYY-MM-DD_HHMMSS` format. This value is reused for deviation manifest naming.

### Step 1: Assess Current State

Discover the base branch from git's upstream tracking (primary) or the explicit
base-branch store file written by `implement-worktree-no-merge` (fallback).

```bash
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Primary: read upstream tracking set by implement-worktree-no-merge
BASE_BRANCH=$(git rev-parse --abbrev-ref @{upstream} 2>/dev/null | sed 's|^[^/]*/||')

if [ -z "$BASE_BRANCH" ]; then
    # Fallback: read explicit file store written by implement-worktree-no-merge
    MAIN_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
    MAIN_ROOT="$(dirname "$MAIN_GIT_DIR")"
    WORKTREE_DIR_NAME=$(basename "$(pwd)")
    STORE_FILE="${MAIN_ROOT}/{{AUTOSKILLIT_TEMP}}/worktrees/${WORKTREE_DIR_NAME}/base-branch"
    BASE_BRANCH=$(cat "${STORE_FILE}" 2>/dev/null)
fi

if [ -z "$BASE_BRANCH" ]; then
    # Last resort: project-level default from config (always available)
    BASE_BRANCH="{{DEFAULT_BASE_BRANCH}}"
    echo "WARNING: Could not determine base branch from git upstream or sidecar file."
    echo "Falling back to project default: ${BASE_BRANCH}"
fi
```

Then assess what has been implemented:
1. Read the plan file to understand the full scope
2. Check what has been implemented so far:
   ```bash
   REMOTE=$(git remote get-url upstream >/dev/null 2>&1 && echo upstream || echo origin)
   git log --oneline $(git merge-base HEAD $REMOTE/${BASE_BRANCH})..HEAD
   git diff --stat $(git merge-base HEAD $REMOTE/${BASE_BRANCH})..HEAD
   ```
3. Compare implemented changes against plan phases to determine:
   - Which phases are complete
   - Which phase is partially complete
   - Which phases haven't started

### Step 2: Targeted Exploration (Only If Needed) (SINGLE MESSAGE)

**Issue ALL Task/Explore subagent calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Only explore systems related to the **remaining** phases. Do NOT re-explore already-completed work. Use Explore subagents for:
- Files that will be modified in remaining phases
- Test patterns for remaining changes
- Integration points affected by remaining work

### Step 3: Continue Implementation

**All commands must run from `{WORKTREE_PATH}`.** Use absolute paths to avoid CWD drift across Bash tool calls.

Initialize a counter before iterating: `PHASES_IMPLEMENTED=0`

NEVER use AskUserQuestion between phase iterations. Each phase begins immediately
after the previous phase completes.

For each remaining/incomplete phase, begin implementation immediately (no announcement):
1. Implement changes
2. Run per-phase verification if plan specifies it
3. Commit per phase if possible
4. Increment the counter: `PHASES_IMPLEMENTED=$((PHASES_IMPLEMENTED + 1))`

Where practical, delegate test updates to subagents to keep main conversation context lean.

### Step 4: Final Verification

Run the project's code quality checks and test suite from the worktree.

```bash
cd {WORKTREE_PATH} && pre-commit run --all-files
cd {WORKTREE_PATH} && \
  AUTOSKILLIT_TEST_FILTER="${AUTOSKILLIT_TEST_FILTER:-conservative}" \
  AUTOSKILLIT_TEST_BASE_REF="${BASE_BRANCH:-}" \
  task test-check
```

If tests fail, fix the issue and re-run.

**Deviation check:** If a fix applied in this step contradicts what the plan specified, record a deviation. If the fix aligns with the plan, skip this sub-step.

Ensure the output directory exists: `mkdir -p {{AUTOSKILLIT_TEMP}}/retry-worktree/` (idempotent — the directory may not exist yet, unlike resolve-failures which already writes to its temp subdirectory).

Read the existing deviation manifest at `{{AUTOSKILLIT_TEMP}}/retry-worktree/deviation_manifest_{SESSION_TS}.json` (if it exists and is valid JSON), or start a new structure. Append a new entry to the `deviations` array and write the complete file:

```json
{
  "schema_version": 1,
  "generated_by": "retry-worktree",
  "generated_at": "<current UTC ISO 8601>",
  "plan_path": "<plan_path from Step 0>",
  "base_branch": "<base_branch from Step 0>",
  "deviations": [
    {
      "what_the_plan_said": "<what the plan required>",
      "what_i_did_instead": "<what you actually implemented>",
      "why": "<why the plan's approach was infeasible — must be falsifiable>",
      "evidence": "<test failure output or diagnostic reasoning>",
      "files_affected": ["<relative paths of files changed>"]
    }
  ]
}
```

**Incremental write:** The manifest is written each time a deviation is recorded, not batched at session end. This ensures the file exists on disk even if the session is terminated by context-limit exhaustion.

### Step 5: Rebase for Squash-and-Merge

```bash
REMOTE=$(git remote get-url upstream >/dev/null 2>&1 && echo upstream || echo origin)
git fetch "$REMOTE"
git rebase "$REMOTE/${BASE_BRANCH}"
```

If conflicts occur, resolve them, `git rebase --continue`, then re-run tests. Report rebase status.

### Step 6: Completion Report

Output to terminal: worktree path, branch name, base branch (`$BASE_BRANCH`), status, summary of changes, and next steps (fast-forward merge then clean up).
Change directory before removing worktree to prevent deleting the cwd.
Always confirm the merge went through before removing worktree.
Do not merge until user confirms first!

Then emit these structured output tokens on their own lines so recipe capture blocks can extract them:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
worktree_path = ${WORKTREE_PATH}
branch_name = ${CURRENT_BRANCH}
phases_implemented = ${PHASES_IMPLEMENTED}
```

If deviations were recorded during Step 4 (i.e., the deviation manifest file exists at `{{AUTOSKILLIT_TEMP}}/retry-worktree/deviation_manifest_{SESSION_TS}.json`), also emit:

`deviation_manifest_path = ${DEVIATION_MANIFEST_PATH}`

Only emit when the manifest file exists. Omit this line entirely when no deviations were recorded.

Where `PHASES_IMPLEMENTED` is the count from Step 3. If Step 3 was skipped entirely
(all phases already complete), emit `phases_implemented = 0`.

## Error Handling

- **Worktree environment missing** — re-create using the project's configured `worktree_setup.command`, or: `task install-worktree`
- **Phase fails** — report which phase and why, offer to fix/retry, skip (if optional), or abort and clean up
- **Tests fail** — implementation is NOT complete. Fix the issue. If truly unfixable, report to user and ask for guidance. Do NOT proceed or mark as complete.
- **Rebase conflicts** — resolve keeping implementation intent intact, re-run full test suite after
