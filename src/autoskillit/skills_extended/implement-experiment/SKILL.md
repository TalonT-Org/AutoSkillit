---
name: implement-experiment
categories: [research]
description: Deploy experiment artifacts in an isolated git worktree following an approved experiment plan, with per-phase commits.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: implement-experiment] Implementing experiment...'"
          once: true
---

# Implement Experiment Skill

Implement an experiment plan in an isolated git worktree. All experiment
artifacts are created inside a single self-contained folder under `research/`.
The worktree is left intact for the orchestrator to run the experiment, test,
and merge separately.

This skill reads the experiment plan and follows its implementation phases.
The plan specifies the directory layout, what scripts to write, what data to
generate, and what environment to set up. This skill builds all of it.

## When to Use

- As the implementation step of the `research` recipe (phase 2)
- After the experiment plan has been approved via GitHub issue

## Arguments

```
/implement-experiment {plan_path}
```

`{plan_path}` — Absolute path to the experiment plan file (required). Scan
tokens after the skill name for the first path-like token (starts with `/`,
`./`, or `.autoskillit/`).

## Critical Constraints

**NEVER:**
- Implement without first exploring affected systems with subagents
- Implement in the main working directory (always use the worktree)
- Force push or perform destructive git operations
- Merge the worktree branch into any branch
- Delete or remove the worktree
- Run the full test suite (the orchestrator handles testing)
- Create experiment files outside the planned `research/` subfolder
- Execute `git merge` commands (all branch content must be applied via
  `git cherry-pick` or `git checkout <branch> -- <file>`)

**ALWAYS:**
- Create a new worktree from the current branch
- Use subagents to deeply understand the codebase context BEFORE implementing
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Follow the implementation phases from the experiment plan
- Put all experiment artifacts in one self-contained `research/` subfolder
- Commit per phase with descriptive messages
- Leave the worktree intact when done

## Context Limit Behavior

If this skill hits the Claude context limit mid-execution, the headless session
terminates with `needs_retry=true` in the tool response. The worktree remains
intact on disk with all commits made up to that point.

The orchestrator should NOT retry this skill — retrying creates a brand-new
worktree, discarding all partial progress. Instead, route to the next step
(run-experiment) which can work with whatever was committed.

## Workflow

### Step 0 — Validate Prerequisites

1. Extract and verify the plan path using **path detection**: scan the tokens
   after the skill name for the first one that starts with `/`, `./`,
   `.autoskillit/temp/`, or `.autoskillit/` — that token is the plan path.
   Ignore any non-path words that appear before it. If no path-like token is
   found, treat the entire argument string as pasted plan content. Verify the
   resolved file exists before proceeding.
2. Read the experiment plan. Extract:
   - The experiment directory name (`research/YYYY-MM-DD-{slug}/`)
   - The planned directory layout
   - Implementation phases
   - Environment requirements (whether an `environment.yml` is needed)
   - What scripts and artifacts to create
3. Check `git status --porcelain` — if dirty, warn user.

### Step 1 — Create Git Worktree

```bash
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
WORKTREE_NAME="research-$(date +%Y%m%d-%H%M%S)"
WORKTREE_PATH="../worktrees/${WORKTREE_NAME}"
git worktree add -b "${WORKTREE_NAME}" "${WORKTREE_PATH}"
WORKTREE_PATH="$(cd "${WORKTREE_PATH}" && pwd)"

# Record the base branch for reliable discovery:
mkdir -p ".autoskillit/temp/worktrees/${WORKTREE_NAME}"
echo "${CURRENT_BRANCH}" > ".autoskillit/temp/worktrees/${WORKTREE_NAME}/base-branch"

# Set upstream tracking if possible:
if ! git fetch origin "${CURRENT_BRANCH}" 2>/dev/null; then
    echo "NOTE: Branch '${CURRENT_BRANCH}' has no remote tracking ref on origin."
fi
if ! git -C "${WORKTREE_PATH}" branch --set-upstream-to="origin/${CURRENT_BRANCH}" "${WORKTREE_NAME}" 2>/dev/null; then
    echo "NOTE: Could not set upstream tracking for '${WORKTREE_NAME}' → 'origin/${CURRENT_BRANCH}'."
fi
```

### Step 1 (cont.) — Emit Structured Tokens Early

Immediately after the worktree is created, output these tokens so the
execution layer can capture them even if context is exhausted later:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
worktree_path = ${WORKTREE_PATH}
branch_name = ${WORKTREE_NAME}
```

### Step 2 — Deep Context Understanding (Subagents)

Before implementing anything, launch subagents (model: "sonnet") to understand
the codebase context needed for the experiment. The following are **minimum
required** — launch as many additional subagents as needed.

**Minimum subagents:**

**Subagent A — Codebase Context:**
> Understand the code areas the experiment will interact with. Identify
> APIs, data structures, functions, and modules that experiment scripts
> will need to reference or call. Report imports, interfaces, and patterns
> the scripts should follow.

**Subagent B — Build & Test Infrastructure:**
> Understand how the project builds, tests, and runs. Identify the build
> system, test framework, benchmark infrastructure, and any relevant
> configuration. Report what the experiment scripts need to integrate with.

**Additional subagents (launch as many as needed):**
- Deeper exploration of specific code areas referenced in the plan
- Understanding specific APIs, types, or interfaces the scripts will use
- Any other codebase investigation needed to write correct experiment code

### Step 3 — Set Up Worktree Environment

Set up the project's development environment in the worktree. Check for
`worktree_setup.command` in `.autoskillit/config.yaml`, a Taskfile with
`install-worktree` task, or detect the project type and run appropriate setup.

```bash
cd "${WORKTREE_PATH}"
# If worktree_setup.command is configured, run it. Otherwise:
task install-worktree   # or equivalent for the project type
```

**All commands from this point must run from `${WORKTREE_PATH}`.** Use
absolute paths to avoid CWD drift across Bash tool calls.

### Step 4 — Implement Phase by Phase

Follow the implementation phases from the experiment plan. The plan specifies
what to create in each phase. Typical phases include:

1. **Directory structure and environment** — create the `research/` subfolder
   layout. If the plan specifies an `environment.yml`, create it and build
   the environment with micromamba.
2. **Data generation** — create data generation scripts, generate datasets,
   verify data properties.
3. **Experiment scripts** — create measurement, benchmark, and analysis
   scripts. Verify they compile/run.
4. **Dry run** — execute the experiment with minimal inputs to verify the
   pipeline works end-to-end.

For each phase:
1. Announce the phase objective
2. Implement the changes
3. Run any verification the plan specifies
4. Commit with a descriptive message. If the project has pre-commit hooks,
   run `pre-commit run --all-files` and stage any auto-fixed files before
   each commit.

The plan is the authority on what phases exist and what each phase creates.
Follow it.

### Step 5 — Copy Experiment Plan into Research Folder

Copy the experiment plan into the research folder for reference:

```bash
cp "${PLAN_PATH}" "${WORKTREE_PATH}/research/YYYY-MM-DD-{slug}/experiment-plan.md"
git add research/ && git commit -m "Add experiment plan to research folder"
```

### Step 6 — Pre-commit Checks

```bash
cd "${WORKTREE_PATH}" && pre-commit run --all-files
```

Fix any formatting or linting issues. Do NOT run the full test suite.

### Step 7 — Handoff Report

Output to terminal:
- **Worktree path:** `${WORKTREE_PATH}`
- **Branch name:** `${WORKTREE_NAME}`
- **Base branch:** the branch the worktree was created from
- **Research folder:** the `research/YYYY-MM-DD-{slug}/` path created
- **Summary:** list of implemented phases and artifacts created

Explicitly state: "Worktree left intact for orchestrator to run experiment and test."

Then emit these structured output tokens:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
worktree_path = ${WORKTREE_PATH}
branch_name = ${WORKTREE_NAME}
```

## Error Handling

- **Worktree creation fails** — check `git worktree list`, suggest `git worktree prune`
- **Environment build fails** — report the error, suggest fixes to environment.yml
- **Script creation fails** — report which phase and why, offer to fix/retry or abort.
  Do NOT clean up the worktree.
- **Pre-commit fails** — fix formatting/linting issues and re-commit
