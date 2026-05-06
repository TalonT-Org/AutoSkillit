---
name: run-experiment
categories: [research]
description: Execute a designed experiment in a worktree and collect structured results. Supports --adjust retry mode.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: run-experiment] Running experiment...'"
          once: true
---

# Run Experiment Skill

Execute an experiment that has been implemented in a worktree. This skill
runs whatever the experiment requires — scripts, benchmarks, custom tooling,
manual procedures, data collection, or any combination. It collects results
and produces a structured results file.

The nature of the experiment is entirely determined by the experiment plan.
This skill does NOT prescribe how experiments should be run — it reads the
plan, executes what the plan describes, and reports what happened.

## When to Use

- As the execution step of the `research` recipe (phase 2)
- After `/autoskillit:implement-worktree-no-merge` has set up experiment code
- When `--adjust` flag is passed, re-run with modified approach after a failure

## Arguments

```
/autoskillit:run-experiment {worktree_path} [--adjust]
```

- `{worktree_path}` — Absolute path to the worktree containing experiment code
  (required). Scan tokens for the first path-like token (starts with `/`, `./`,
  or `.autoskillit/`).
- `--adjust` — Optional flag indicating this is a retry after a previous failure.
  When present, read the previous results/errors from `{{AUTOSKILLIT_TEMP}}/run-experiment/`
  and adjust the approach before re-running.

## Critical Constraints

**NEVER:**
- Modify files outside the worktree
- Merge the worktree — leave it intact for the orchestrator
- Skip result collection — every run must produce structured output
- Assume what kind of experiment this is — read the plan and follow it
- Commit files under `{{AUTOSKILLIT_TEMP}}/` — this directory is gitignored working space, NOT for version control. Do not use `git add -f` or `git add --force` to bypass the gitignore.
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write results to `{{AUTOSKILLIT_TEMP}}/run-experiment/` in the worktree (disk only, never committed)
- Report failures with enough detail for the `--adjust` retry to fix them

## Context Limit Behavior

When context is exhausted mid-execution, experiment results may be partially written
to `{{AUTOSKILLIT_TEMP}}/run-experiment/`. The recipe routes to `on_context_limit`,
abandoning the partial experiment run.

**Before emitting structured output tokens:**
1. If results were not fully written, emit `experiment_results = ` (empty) as a fallback
2. The orchestrator's `on_context_limit` route handles the partial state; the
   downstream `--adjust` retry can restart the experiment from scratch

## Workflow

### Step 1 — Discover Experiment

Read the experiment plan from `{{AUTOSKILLIT_TEMP}}/experiment-plan.md` in the
worktree (or the project root, checking both locations). This was saved by the
recipe's `save_experiment_plan` step from the approved GitHub issue.

Also scan the worktree for experiment-related files:
- Scripts, benchmarks, test files, or tools added by `implement-worktree-no-merge`
- Configuration files for the experiment
- Data generators, fixtures, or input files

Understand what the experiment requires before attempting to run anything.

### Step 2 — Pre-flight Check

Before running the experiment:
1. Verify the project builds or that prerequisites are met.
2. Verify experiment artifacts exist (scripts, data, dependencies).
3. If `--adjust` flag is set, read previous results from
   `{{AUTOSKILLIT_TEMP}}/run-experiment/` and identify what went wrong.

Launch subagents (model: "sonnet") if needed to investigate the experiment
setup, resolve dependencies, or research how to use specific tools mentioned
in the plan.

### Data Manifest Verification (mandatory)

Before executing any hypothesis:

1. **Read the Data Manifest** from the experiment plan's YAML frontmatter
   (`data_manifest` field). If no frontmatter or no `data_manifest` field exists,
   log a warning and proceed with best-effort artifact checks.

2. **For each `data_manifest` entry**, verify:
   - If `location` is specified: the path exists and is non-empty
   - If `verification` criteria are specified: evaluate them (e.g., file count, size)
   - If `acquisition` command is specified and data is missing: attempt to run
     the acquisition command. If it fails, mark the entry as BLOCKED.

3. **Produce a data readiness table:**
   ```
   | Hypothesis | Source Type | Location | Status |
   |------------|-------------|----------|--------|
   | H1, H2     | synthetic   | in-script | READY  |
   | H5         | external    | temp/merfish_100k/ | BLOCKED — directory empty |
   ```

4. **If any entry the plan said would be acquired is BLOCKED:**
   - Do NOT silently degrade to N/A
   - Emit the structured output token `blocked_hypotheses` listing all blocked entries
   - Set the results file `## Status` to `FAILED`
   - Exit with a clear error message: "Pre-flight blocked: planned data for {hypotheses}
     is unavailable. Data Manifest declared acquisition via {method} but verification
     failed."

This replaces the current behavior of silently marking missing-data hypotheses as N/A.
When the plan declared acquisition steps for data and those steps did not produce the
data, this is a pipeline failure — not a pipeline-level degradation.

### Step 3 — Execute Experiment

Read `env_mode` from context (set by `setup-environment` earlier in the
recipe). Dispatch execution based on the mode:

**`env_mode = docker`:**

The Docker image `research-{slug}` was pre-built by `setup-environment`.
Execute the experiment inside the container:

```bash
RESEARCH_DIR=$(ls -d "${WORKTREE_PATH}"/research/*/ 2>/dev/null | head -1)
SLUG=$(basename "${RESEARCH_DIR%/}")
docker run --rm -v "${RESEARCH_DIR}:/workspace" "research-${SLUG}" \
  bash -c "cd /workspace && python scripts/run.py"
```

Adjust the entry-point command to match the actual script from the experiment
plan. If the research directory contains a `Taskfile.yml` with a
`run-experiment` task, prefer `task run-experiment` inside the container.

**`env_mode = micromamba-host`:**

A host micromamba environment `experiment-{slug}` was created by
`setup-environment`. Execute the experiment inside that environment:

```bash
RESEARCH_DIR=$(ls -d "${WORKTREE_PATH}"/research/*/ 2>/dev/null | head -1)
SLUG=$(basename "${RESEARCH_DIR%/}")
cd "${RESEARCH_DIR}"
micromamba run -n "experiment-${SLUG}" python scripts/run.py
```

Adjust the entry-point command to match the actual script from the experiment
plan.

**`env_mode = unavailable`:**

No suitable environment could be provisioned. Emit the `blocked_experiment`
structured output token and set the results status to FAILED:

```
blocked_experiment = env_mode is unavailable — setup-environment could not provision docker or micromamba-host
```

Write a results file with `## Status: FAILED` and the reason, then proceed
to Step 5 (Save Results) to emit the `results_path` token.

**`env_mode = none`:**

Standard environment — no container or micromamba needed. Run the experiment
directly in the worktree using the system Python:

```bash
RESEARCH_DIR=$(ls -d "${WORKTREE_PATH}"/research/*/ 2>/dev/null | head -1)
cd "${RESEARCH_DIR}" && python scripts/run.py
```

---

If the plan specifies multiple configurations or comparisons, execute all of
them under the dispatched environment mode and collect results for each.

### Step 4 — Collect Results

Structure the results as a markdown file:

```markdown
# Experiment Results: {title}

## Run Metadata
- Date: {YYYY-MM-DD HH:MM:SS}
- Worktree: {worktree_path}
- Commit: {git rev-parse HEAD}
- Environment: {relevant version info}

## Configuration
{Parameters used for this run — from the experiment plan}

## Results

{Present the data collected. Use tables, code blocks, or whatever format
best represents the measurements. Include raw data when feasible.}

## Observations
{Notable patterns, anomalies, unexpected behaviors, anything worth noting}

## Recommendation
{Based on the evidence collected, what does this suggest? This is the
experimenter's interpretation — the generate-report skill will synthesize
the final conclusions.}

## Status
{One of: CONCLUSIVE_POSITIVE | CONCLUSIVE_NEGATIVE | INCONCLUSIVE | FAILED}
{Brief justification for the status}
```

### Step 5 — Save Results

1. Save results to:
   `{{AUTOSKILLIT_TEMP}}/run-experiment/results_{topic}_{YYYY-MM-DD_HHMMSS}.md`
   (relative to the current working directory) within the worktree.
2. Also save any raw data files (CSV, JSON, logs) to the same directory.
3. Do NOT `git add` or commit files under `{{AUTOSKILLIT_TEMP}}/`. This directory
   is gitignored working space. The files persist on the worktree filesystem
   for `generate-report` to read. Final results are published to `research/` by
   the `generate-report` skill.

After saving, emit the structured output token as the very last line of your
text output:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
results_path = {absolute_path_to_results_file}
```

**When pre-flight blocks hypotheses due to missing planned data:**

```
blocked_hypotheses = H5: MERFISH data missing at temp/merfish_100k/ (acquisition: generate_merfish_subset.py --n 100000)
```

This token is emitted ONLY when the pre-flight gate fails due to data declared in the
Data Manifest being inaccessible. It is NOT emitted during normal execution.

When `blocked_hypotheses` is emitted, `results_path` still points to the results file
with `## Status: FAILED`.

## Adjust Mode (--adjust)

When `--adjust` is passed, this is a retry after a previous execution failed.

1. Read previous results from `{{AUTOSKILLIT_TEMP}}/run-experiment/` in the worktree
2. Identify the failure mode
3. Make targeted adjustments to address the specific failure
4. Re-run the experiment with adjustments
5. Document what was changed and why in the results file

Do NOT redesign the entire experiment — make minimal adjustments to address
the specific failure. If the experiment design itself is fundamentally flawed,
return a FAILED status so the recipe can escalate.
