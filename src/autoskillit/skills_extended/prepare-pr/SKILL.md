---
name: prepare-pr
categories: [github]
description: Preparation executor for pull-request metadata. ALWAYS invoke this skill when instructed to prepare PR metadata. Do not read plans or classify files directly — use this skill first to load the preparation workflow.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: prepare-pr] Preparing PR metadata and arch-lens context...'"
          once: true
---

# Prepare PR

Read plan(s), analyze changed files, select relevant arch-lens slugs, write one PR context
file per lens, and write a PR prep file for downstream use by `compose-pr`.

This skill Does NOT invoke arch-lens skills or any other sub-skills. It is Part 1 of 3
in the decomposed PR flow (prepare → run_arch_lenses → compose).

## Arguments

`/autoskillit:prepare-pr {plan_paths} {run_name} {base_branch} [closing_issue] [conflict_report_path]`

- **plan_paths** — Comma-separated absolute paths to implementation plan markdown files
- **run_name** — Branch name prefix (e.g. `impl`, `feature/123`, `fix/653`); determines
  `[FEATURE]`/`[FIX]` prefix
- **base_branch** — PR target branch
- **closing_issue** (optional) — GitHub issue number for requirements fetch + `Closes #N`
- **conflict_report_path** (optional) — Absolute path to conflict resolution report

## Critical Constraints

**NEVER:**
- Invoke arch-lens skills or any other sub-skills
- Create files outside `{{AUTOSKILLIT_TEMP}}/prepare-pr/`
- Fail if closing_issue is absent or gh is unavailable — degrade gracefully
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Emit all three output tokens (`prep_path`, `selected_lenses`, `lens_context_paths`)
- Classify changed files as new (★) vs modified (●)

## Context Limit Behavior

When context is exhausted mid-execution, prep files may be partially written.
The recipe routes to `on_context_limit` (typically `release_issue_failure`),
abandoning the PR preparation.

**Before emitting structured output tokens:**
1. If prep files were not fully written, emit `prep_path = ` (empty) as a fallback
2. Emit whatever was completed; the orchestrator handles the context-limit route

## Workflow

### Step 0: Parse Arguments and Initialize

Parse positional arguments:
- arg[1] = `plan_paths` (comma-separated)
- arg[2] = `run_name`
- arg[3] = `base_branch`
- arg[4] = `closing_issue` (optional — may be absent or empty string)
- arg[5] = `conflict_report_path` (optional — may be absent or empty string)

Derive `feature_branch` (`git rev-parse --abbrev-ref HEAD`).
Create temp dir (relative to the current working directory):
```bash
mkdir -p {{AUTOSKILLIT_TEMP}}/prepare-pr/
FEATURE_BRANCH=$(git rev-parse --abbrev-ref HEAD)
ts=$(date +%Y-%m-%d_%H%M%S)
CLOSING_ISSUE="${4:-}"
```
Generate timestamp `ts` from the bash block above.

### Step 1: Fetch Requirements from Closing Issue

- If `closing_issue` is absent or empty string: skip — set `requirements_section = ""`.
- Fetch issue body:
  ```bash
  gh issue view $CLOSING_ISSUE --json body -q .body
  ```
- Extract the `## Requirements` section: everything from `## Requirements` to the next
  `## ` heading or end of body, whichever comes first.
- If no `## Requirements` section found: set `requirements_section = ""`.
- Skip gracefully if `gh` is unavailable — `requirements_section` remains `""`.

### Step 2: Extract PR Title from Plans

Read all plan files. For each, extract the first `# ` heading line, strip the `# ` prefix,
and strip any trailing `— PART [A-Z] ONLY` suffix.

- **Single plan:** Use the heading directly as `task_title`.
- **Multiple plans:** Spawn a subagent (Task tool, model: sonnet) with all extracted
  headings. Instruct it to synthesize a single concise PR title (under 70 characters).

**PR Title Prefix (derived from run_name):**

```bash
case "$RUN_NAME" in
  feature*) TITLE="[FEATURE] $BASE_TITLE" ;;
  fix*)     TITLE="[FIX] $BASE_TITLE" ;;
  *)        TITLE="$BASE_TITLE" ;;
esac
```

### Step 3: Load Conflict Resolution Report

- If `conflict_report_path` is absent or empty: skip — `conflict_resolution_table = ""`.
- Read the file at `conflict_report_path`.
- Extract the `## Per-File Resolution Decisions` table.
- Skip gracefully if the file does not exist.

### Step 4: Classify Changed Files

Run git diff to classify changed files:

```bash
git diff --name-only $BASE_BRANCH..$FEATURE_BRANCH
git diff --diff-filter=A --name-only $BASE_BRANCH..$FEATURE_BRANCH  # new_files
git diff --diff-filter=M --name-only $BASE_BRANCH..$FEATURE_BRANCH  # modified_files
```

Store as separate lists: `new_files` (added, ★) and `modified_files` (modified, ●).

### Step 5: Select Arch-Lens Slugs

Spawn a subagent (Task tool, model: sonnet) with the list of changed file paths and the
following lens menu:

```
c4-container, concurrency, data-lineage, deployment, development,
error-resilience, module-dependency, operational, process-flow,
repository-access, scenarios, security, state-lifecycle
```

Instruct the subagent to return 1–3 lens slugs. Only include a lens if at least one
changed file maps to that lens's concern.

**Development lens guard:** The `development` lens must ONLY be selected if at least one
changed file matches a build/test configuration pattern: `pyproject.toml`, `Taskfile*`,
`conftest.py`, `.github/workflows/*`, `Makefile`, `setup.cfg`, `setup.py`, `tox.ini`,
`noxfile.py`, or files under a `ci/` directory. If no changed file matches these patterns,
do NOT select the `development` lens regardless of other criteria.

Output: comma-separated slug list → `selected_lens_slugs`.

### Step 6: Write Context Files per Lens

Use the Write tool (not Bash heredocs or cat) to create each context file.

For each selected slug, write one context file to
`{{AUTOSKILLIT_TEMP}}/prepare-pr/pr_arch_lens_context_{slug}_{ts}.md`:

```markdown
# PR Context — Changed Files

This diagram is for a Pull Request. Focus the diagram on the areas of the codebase
affected by these changes. Do not produce a generic whole-project view.

## New files (use ★ prefix on these nodes):
{list of new_files, or "None"}

## Modified files (use ● prefix on these nodes):
{list of modified_files, or "None"}

## Instructions:
- Focus exploration and the diagram on the architectural areas these files belong to
- Use ★ prefix on nodes representing new files/components
- Use ● prefix on nodes representing modified files/components
- Leave unchanged components unmarked (include only if needed for context/connectivity)
- The diagram should help PR reviewers understand the architectural impact
```

Record absolute paths in `lens_context_paths` list (comma-separated).

### Step 7: Read Plan Summaries

Read `## Summary` from each plan file. Store plan summaries for the prep file.

### Step 8: Write PR Prep File

Use the Write tool (not Bash heredocs or cat) to create the prep file.

Write PR prep file to `{{AUTOSKILLIT_TEMP}}/prepare-pr/pr_prep_{ts}.md`:

```markdown
# PR Prep: {task_title}

## Metadata

- feature_branch: {feature_branch}
- base_branch: {base_branch}
- closing_issue: {issue_number or ""}
- plan_paths: {comma-separated}

## Title

{task_title}

## Plan Summary

{for single plan: ## Summary section content}
{for multiple plans: individual summaries with group headings}

## Requirements

{requirements_section or ""}

## Conflict Resolution Table

{conflict_resolution_table or ""}

## Changed Files

### New (★):
{new_files list or "None"}

### Modified (●):
{modified_files list or "None"}

## Selected Lenses

{comma-separated slugs}

## Lens Context Paths

{comma-separated absolute paths}
```

## Output

Emit these structured output tokens (literal plain text, no markdown decoration).
All output paths are absolute (resolve `{{AUTOSKILLIT_TEMP}}/prepare-pr/` relative to
the current working directory using `$(pwd)`):

```
prep_path = /absolute/path/{{AUTOSKILLIT_TEMP}}/prepare-pr/pr_prep_{ts}.md
selected_lenses = module-dependency,process-flow
lens_context_paths = /abs/ctx_module-dependency_{ts}.md,/abs/ctx_process-flow_{ts}.md
```
