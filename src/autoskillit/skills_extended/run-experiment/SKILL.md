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
/run-experiment {worktree_path} [--adjust]
```

- `{worktree_path}` — Absolute path to the worktree containing experiment code
  (required). Scan tokens for the first path-like token (starts with `/`, `./`,
  or `.autoskillit/`).
- `--adjust` — Optional flag indicating this is a retry after a previous failure.
  When present, read the previous results/errors from `.autoskillit/temp/run-experiment/`
  and adjust the approach before re-running.

## Critical Constraints

**NEVER:**
- Modify files outside the worktree
- Merge the worktree — leave it intact for the orchestrator
- Skip result collection — every run must produce structured output
- Assume what kind of experiment this is — read the plan and follow it
- Commit files under `.autoskillit/temp/` — this directory is gitignored working space, NOT for version control. Do not use `git add -f` or `git add --force` to bypass the gitignore.

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write results to `.autoskillit/temp/run-experiment/` in the worktree (disk only, never committed)
- Report failures with enough detail for the `--adjust` retry to fix them

## Workflow

### Step 1 — Discover Experiment

Read the experiment plan from `.autoskillit/temp/experiment-plan.md` in the
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
   `.autoskillit/temp/run-experiment/` and identify what went wrong.

Launch subagents (model: "sonnet") if needed to investigate the experiment
setup, resolve dependencies, or research how to use specific tools mentioned
in the plan.

### Step 3 — Execute Experiment

Run the experiment as described in the plan. The experiment could be anything:
scripts, benchmarks, data collection, manual measurements, tool invocations,
custom pipelines, or any other procedure. Follow the plan's execution protocol.

If the plan specifies multiple configurations or comparisons, execute all of
them and collect results for each.

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
experimenter's interpretation — the write-report skill will synthesize
the final conclusions.}

## Status
{One of: CONCLUSIVE_POSITIVE | CONCLUSIVE_NEGATIVE | INCONCLUSIVE | FAILED}
{Brief justification for the status}
```

### Step 5 — Standardized Metrics Assessment

After collecting experiment-specific results, run the canonical metrics assessment
CLI to produce standardized JSON and markdown artifacts:

> **Prerequisite:** If `.npz` fixture files have not been generated yet, run `python tests/generate_fixtures.py` first (matching the `#[ignore]` annotation on fixture-dependent tests).

```bash
cd "$WORKTREE_PATH"
cargo test --features testing --test test_metrics_assess -- assess_accuracy --include-ignored --no-capture
```

This writes two files to `.autoskillit/temp/run-experiment/` (relative to the worktree):
- `accuracy_metrics.json` — machine-readable metrics with per-metric pass/fail status
- `accuracy_metrics.md` — human-readable summary table

**If the experiment affects parity** (i.e., it involves `ComputeMode`, SIMD paths,
scaling behavior, or eigenvector sign normalization), also run:

```bash
cargo test --features testing --test test_metrics_assess -- assess_parity --include-ignored --no-capture
```

This writes:
- `parity_metrics.json`
- `parity_metrics.md`

**Add a `## Standardized Metrics` section** to the results markdown file (in Step 4's
template, after `## Recommendation`):

```markdown
## Standardized Metrics

Accuracy assessment: see `accuracy_metrics.md`
{If parity run: Parity assessment: see `parity_metrics.md`}

{Inline the summary table from the .md file, or note any failed metrics here.}
```

If `cargo test` fails (e.g., fixtures not generated), note the failure in the
`## Standardized Metrics` section and continue — a metrics CLI failure does not
block result collection.

### Step 6 — Save Results

1. Save results to:
   `.autoskillit/temp/run-experiment/results_{topic}_{YYYY-MM-DD_HHMMSS}.md`
   within the worktree.
2. Also save any raw data files (CSV, JSON, logs) to the same directory.
3. Do NOT `git add` or commit files under `.autoskillit/temp/`. This directory
   is gitignored working space. The files persist on the worktree filesystem
   for `write-report` to read. Final results are published to `research/` by
   the `write-report` skill.

After saving, emit the structured output token as the very last line of your
text output:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
results_path = {absolute_path_to_results_file}
```

## Adjust Mode (--adjust)

When `--adjust` is passed, this is a retry after a previous execution failed.

1. Read previous results from `.autoskillit/temp/run-experiment/` in the worktree
2. Identify the failure mode
3. Make targeted adjustments to address the specific failure
4. Re-run the experiment with adjustments
5. Document what was changed and why in the results file

Do NOT redesign the entire experiment — make minimal adjustments to address
the specific failure. If the experiment design itself is fundamentally flawed,
return a FAILED status so the recipe can escalate.
