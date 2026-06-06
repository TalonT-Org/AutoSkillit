---
name: implement-worktree-no-merge
uses_capabilities: [agent_model, cross_skill_ref, run_skill, git_metadata_write]
activate_deps: [write-recipe]
description: Implementation executor. ALWAYS invoke this skill when instructed to implement a plan in a worktree. Do not read the plan or edit files directly — use this skill first to load the full implementation workflow.
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

- MCP orchestrator calls this via `run_skill`
- Orchestrator wants to control test/merge gates independently

## Arguments

`{plan_path}`   — Absolute path to the implementation plan file (required)

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Implement without first exploring affected systems with subagents
- Implement in the main working directory (always use the worktree)
- Force push or perform destructive git operations
- Merge the worktree branch into any branch
- Delete or remove the worktree
- Run the full test suite (the orchestrator handles testing) — NEVER invoke pytest, python -m pytest, or any test runner directly
- Rebase onto the base branch
- Clean up the worktree environment
- Re-run tests just to see failures — grep the saved output file instead
- Blame pre-commit or lint failures on "pre-existing issues" — ALL pre-commit checks must pass on the committed code
- Pipe test output through `tail`, `head`, or other truncation commands
- **Execute `git merge` commands** (including `--no-ff`, `--no-commit`, or any variant). All branch content must be applied via `git cherry-pick <commit>` for individual commits or `git checkout <branch> -- <file>` for specific files. `merge_worktree` requires linear commit history — merge commits cannot be rebased and will cause `WORKTREE_INTACT_MERGE_COMMITS_DETECTED` failure.
- Run subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Create a new worktree from the current branch
- Use subagents to deeply understand affected systems BEFORE implementing
- Spawn all subagents via `Agent(model="sonnet")`
- Implement one phase at a time
- Commit per phase with descriptive messages
- Leave the worktree intact when done
- **Read before editing**: Before issuing an `Edit` call on any file, ensure you have issued a `Read` on that file earlier in this session. Claude Code rejects `Edit` on unread files — the retry wastes a full API turn at current context size. If you are uncertain whether a file was read, issue a targeted `Read` (offset + limit to the region you plan to edit) rather than risk an error. **Note:** Reads performed by subagents (Task/Agent) do NOT satisfy this requirement — they run in a child session whose reads are invisible to the parent. If a file was only read inside a subagent, you must Read it again in this main session before calling Edit.
- **Read files fully**: When reading a file to understand it in full, read it in a single call without a `limit` parameter. Do not paginate files with sequential offset reads — read once completely. Use `limit`/`offset` only for targeted section reads of files you have already read in full.
- Issue all Task calls in a single message to maximize parallelism

## Context Limit Behavior

If this skill hits the Claude context limit mid-execution, the headless session
terminates with `needs_retry=true` in the tool response. The worktree remains
intact on disk with all commits made up to that point.

The orchestrator **must not** retry this skill when `needs_retry=true`. Retrying
creates a brand-new timestamped worktree, discarding all partial progress.

Correct orchestration on `needs_retry=true`:
- Route immediately to `/autoskillit:retry-worktree` (via `retry.on_exhausted`)
- The `run_skill` response now includes `worktree_path` as a top-level JSON
  field when `needs_retry=true`. The orchestrator reads it from
  `result.worktree_path` — no filesystem search is needed.
- Use `max_attempts: 0` on this step's `retry` block to ensure immediate escalation

## Workflow

### Step 0: Validate Prerequisites

1. Extract and verify the plan path using **path detection**: scan the tokens
   after the skill name for the first one that starts with `/`, `./`, `{{AUTOSKILLIT_TEMP}}/`,
   or `.autoskillit/` — that token is the plan path. Ignore any non-path words
   that appear before it (orchestrators sometimes prepend descriptive text such
   as "the verified plan"). When no path-like token is present, treat the entire
   argument string as pasted plan content. Verify the resolved file exists before
   proceeding; if it does not, abort with:
   `"Plan file not found: {path}. Correct format: /autoskillit:implement-worktree-no-merge <plan_path>"`
2. **Check for dry-walkthrough verification:** Read the first line of the plan file. If it does not contain exactly `Dry-walkthrough verified = TRUE`:
   - Display warning: "WARNING: This plan has NOT been validated with a dry-walkthrough. Implementation may encounter issues that could have been caught beforehand."
   - Use `AskUserQuestion` to prompt: "Do you want to continue without dry-walkthrough validation?"
   - If user declines, abort and suggest running `/autoskillit:dry-walkthrough` first

   **Note:** The server-side gate also verifies that the plan file is at its original
   `make-plan/` or `rectify/` location. Plans copied to other directories (e.g.,
   `dry-walkthrough/`) are rejected even if they carry the verification marker.
3. Check `git status --porcelain` — if dirty, warn user
4. Parse plan: phases, files per phase, verification commands
5. **Multi-Part Plan Detection:** Examine the plan filename. If it contains `_part_` (e.g., `_part_a`, `_part_b`, `_part_1`):
   - Extract the part identifier (A, B, C… or number) from the suffix.
   - **SCOPE FENCE — MANDATORY:** Before any exploration or implementation begins, output the following constraint:
     > "🚧 SCOPE FENCE ACTIVE: I am implementing PART {X} ONLY. I MUST NOT open, read, or execute any other part files, regardless of what I encounter in {{AUTOSKILLIT_TEMP}}/ or any other directory. Sibling part files are out of scope for this entire session."
   - When launching subagents in Step 2, include this fence instruction explicitly in each subagent prompt so that the subagents do not open, read, or reference sibling part files.

### Step 1: Create or Detect Git Worktree

First, check if you are already inside a pre-created linked worktree (the recipe orchestrator may have created one via `run_cmd` before dispatching this skill):

```bash
if [ -f ".git" ]; then
  echo "PRE_CREATED_WORKTREE=true"
  echo "WORKTREE_PATH=$(pwd)"
  echo "BRANCH_NAME=$(git branch --show-current)"
else
  echo "PRE_CREATED_WORKTREE=false"
fi
```

- **If `PRE_CREATED_WORKTREE=true`**: The skill is already inside a linked worktree (`.git` is a file, not a directory). Set `WORKTREE_PATH` to the current working directory and `BRANCH_NAME` from the output. **Skip worktree creation** — proceed directly to Step 1 (cont.).
- **If `PRE_CREATED_WORKTREE=false`**: The skill is running in the main repo (Claude Code backward-compat path). Create a new worktree:

```bash
WORKTREE_NAME="impl-{plan_name}-$(date +%Y%m%d-%H%M%S)"
eval "$(bash "{{AUTOSKILLIT_SCRIPTS}}/create_impl_worktree.sh" "${WORKTREE_NAME}" "{{AUTOSKILLIT_TEMP}}")"
```

**Read `WORKTREE_PATH` from that output** — it is an absolute path to the worktree (a sibling directory outside this repo). Use this literal path in every subsequent `cd` and file reference. Shell variables do not persist across Bash tool calls.

### Step 1 (cont.): Emit Structured Tokens Early

Immediately after the worktree is created, output these tokens on their own
lines so the execution layer can capture them from `assistant_messages` even
if context is exhausted before Step 6:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
worktree_path = ${WORKTREE_PATH}
branch_name = ${BRANCH_NAME}
has_implementation_progress = true
```

**Why emit early?** If context exhaustion occurs during Steps 2–5, the
execution layer scans `assistant_messages` for `worktree_path=` and surfaces
it as a top-level field in the `run_skill` JSON response. The orchestrator
reads this field directly without filesystem discovery heuristics.

### Step 2: Deep System Understanding (Subagents) (SINGLE MESSAGE)

**Issue ALL Task tool calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

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

### Step 4: Implement Phase by Phase

For each phase, begin implementation immediately (no announcement):
1. Implement changes guided by understanding from Step 2
2. Run per-phase verification if plan specifies it
3. Commit per phase with descriptive messages. If the project has pre-commit
   hooks, run `pre-commit run --all-files` and stage any auto-fixed files
   before each commit.

Where practical, delegate test updates to subagents to keep main conversation context lean.

### Step 5: Run Pre-commit Checks

```bash
cd "${WORKTREE_PATH}" && pre-commit run --all-files
```

Fix any formatting or linting issues. Do NOT run any tests — NEVER invoke pytest, python -m pytest, or any test runner directly. Use `task test-check` if explicitly instructed to verify tests.

If pre-commit auto-fixes files, stage them and create a **new** commit (do NOT use `--amend`):
```bash
git add -u && git commit -m 'style: apply pre-commit auto-fixes'
```

### Step 5.5: Completeness Self-Check (Conflict Resolution Plans Only)

If the plan contains a `PR Changes Inventory` section, perform a completeness check before
handoff:

1. Extract the **Category C — Clean Carry-Overs** file list from the plan.
2. Run `git diff {base_branch}...HEAD --name-only` to get all files in the implementation.
3. For each Category C file, verify it appears in the diff.
4. If any Category C files are missing from the diff:
   - Fetch them from the PR branch: `git show origin/{pr_branch}:{file_path}`
   - Write them to the worktree and commit: `fix: carry over {file_path} from PR branch`
   - Re-run the check until all Category C files are present.

This guard prevents silent data loss: Category C files are PR-only changes that require no
conflict resolution and must be preserved in full.

### Step 6: Handoff Report

Output to terminal:
- **Worktree path:** `${WORKTREE_PATH}`
- **Branch name:** `${WORKTREE_NAME}`
- **Base branch:** the branch the worktree was created from
- **Summary:** list of implemented phases and key changes

Explicitly state: "Worktree left intact for orchestrator to test and merge."

Then emit these structured output tokens on their own lines so recipe capture blocks can extract them:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
worktree_path = ${WORKTREE_PATH}
branch_name = ${BRANCH_NAME}
has_implementation_progress = true
```

**If this is a `_part_` plan file:** The orchestrator MUST merge this worktree
(`merge_worktree`) into the base branch BEFORE invoking
`implement-worktree-no-merge` for the next part. Part N+1's worktree must be
created from the post-merge state of the base branch, not from Part N's base
commit. This is a global sequencing rule — it applies even when operating
off-recipe.

## Error Handling

- **Worktree creation fails** — check `git worktree list`, suggest `git worktree prune`
- **Phase fails** — report which phase and why, offer to fix/retry, skip (if optional), or abort. Do NOT clean up the worktree.
- **Pre-commit fails** — fix formatting/linting issues and create a new commit (do NOT use `--amend`)
