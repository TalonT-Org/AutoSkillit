---
name: prepare-research-pr
categories: [research]
description: >
  Reads a research report and experiment plan, synthesizes a recommendation,
  selects 1-2 exp-lens lenses, writes a context file per lens, and writes a
  PR prep file. Does NOT open a PR. Part 1 of 3 in the decomposed research-PR flow.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: prepare-research-pr] Preparing research PR metadata...'"
          once: true
---

# Prepare Research PR

Reads a completed experiment report and plan, synthesizes a directional
recommendation, selects 1-2 exp-lens lenses, writes a context file per lens,
and writes a PR prep file for downstream use by `compose-research-pr`.
Does NOT invoke any exp-lens skills or create a PR.

## Arguments

`/autoskillit:prepare-research-pr {report_path} {experiment_plan_path} {worktree_path} {base_branch}`

- **report_path** — Absolute path to `research/{slug}/README.md` (post-rename by `commit_research_artifacts`)
- **experiment_plan_path** — Absolute path to the experiment plan (from `${{ context.experiment_plan }}`)
- **worktree_path** — Worktree root directory
- **base_branch** — PR target branch

## Critical Constraints

**NEVER:**
- Invoke exp-lens skills — they are run in separate sessions by the recipe orchestrator
- Create files outside `{{AUTOSKILLIT_TEMP}}/prepare-research-pr/` (relative to the current working directory)
- Fail silently — always emit all three output tokens before `%%ORDER_UP%%`

**ALWAYS:**
- Use Agent subagents (not slash commands) for reading and synthesis
- Emit `prep_path`, `selected_lenses`, and `lens_context_paths` before `%%ORDER_UP%%`
- Write all output paths as absolute paths

## Lens Selection Table

| experiment_type | Primary Lens | Secondary Lens (optional) |
|---|---|---|
| benchmark | exp-lens-fair-comparison | exp-lens-estimand-clarity |
| configuration_study | exp-lens-iterative-learning | exp-lens-sensitivity-robustness |
| causal_inference | exp-lens-causal-assumptions | exp-lens-estimand-clarity |
| robustness_audit | exp-lens-severity-testing | exp-lens-validity-threats |
| exploratory | exp-lens-estimand-clarity | exp-lens-exploratory-confirmatory |

## Experiment Status Badges

- `CONCLUSIVE_POSITIVE` — hypothesis supported; results meet success criteria
- `CONCLUSIVE_NEGATIVE` — hypothesis refuted; results clearly below threshold
- `INCONCLUSIVE` — results ambiguous; insufficient signal or mixed evidence
- `FAILED` — experiment could not be completed; results invalid

---

## Workflow

### Step 0: Parse arguments and create temp dir

Parse positional args:
- arg[1] = report_path
- arg[2] = experiment_plan_path
- arg[3] = worktree_path
- arg[4] = base_branch

Derive `feature_branch`:

    FEATURE_BRANCH=$(git -C "{worktree_path}" rev-parse --abbrev-ref HEAD)

Create temp directory:

    mkdir -p {{AUTOSKILLIT_TEMP}}/prepare-research-pr/

Generate a timestamp `ts` (format: `YYYY-MM-DD_HHMMSS`) for unique file naming.

### Step 1: Read report via Agent subagent

Spawn an **Explore** subagent to read `{report_path}` and extract:
- Executive summary
- Conclusions
- Recommendations
- Results section (findings table, key metrics)
- Experiment status badge (CONCLUSIVE_POSITIVE / CONCLUSIVE_NEGATIVE / INCONCLUSIVE / FAILED)
- Title from the first H1 heading or `title:` field

Store extracted content as `report_content`.

### Step 2: Read experiment plan via Agent subagent

Spawn an **Explore** subagent to read `{experiment_plan_path}` and extract:
- `experiment_type` (benchmark / configuration_study / causal_inference / robustness_audit / exploratory)
- H0 (null hypothesis)
- H1 (alternative hypothesis)
- Independent Variables (IV) table
- Dependent Variables / Metrics (DV) table
- Controlled Variables list
- Success Criteria
- Methodology section

Store extracted content as `plan_content`.

### Step 3: Synthesize recommendation via sonnet subagent

Spawn a **sonnet** subagent with the report's Conclusions, Recommendations, and
experiment status. Produce a 1-3 sentence directional recommendation (inverted pyramid —
most important finding first). Store as `recommendation`.

### Step 4: Select lenses via sonnet subagent

Spawn a **sonnet** subagent with `experiment_type` and the plan's IV/DV structure.
Select 1-2 exp-lens slugs using the Lens Selection Table above.

Output: a list of 1-2 slugs, e.g. `["fair-comparison", "estimand-clarity"]`.
Store as `selected_lens_slugs`.

### Step 5: Write one context file per lens

For each slug in `selected_lens_slugs`, write a context file to:

    {{AUTOSKILLIT_TEMP}}/prepare-research-pr/exp_lens_context_{slug}_{ts}.md

The context file must contain (enough for the lens to build its diagram without
reading the entire CWD):

```markdown
# Experiment Context: {slug} Lens

## Hypotheses

| | Hypothesis |
|---|---|
| H0 | {null hypothesis} |
| H1 | {alternative hypothesis} |

## Independent Variables

| Variable | Values / Levels | Role |
|----------|-----------------|------|
{rows from plan's IV table}

## Dependent Variables / Metrics

| Metric | Unit | Threshold |
|--------|------|-----------|
{rows from plan's DV table}

## Controlled Variables

{list from plan}

## Success Criteria

{from plan}

## Experiment Type

{experiment_type}
```

Record the absolute paths in `lens_context_paths` list (same order as slugs).

### Step 6: Build results summary

From the report's Results section, construct:
- Findings table (Finding / Result / Confidence)
- Experiment status badge
- Key metrics (if standardized metrics referenced in the report, include inline)

Store as `results_summary` (plain text, will be embedded in prep file).

### Step 7: Write PR prep file

Write to `{{AUTOSKILLIT_TEMP}}/prepare-research-pr/pr_prep_{ts}.md`:

```markdown
# PR Prep: {title from report}

## Metadata

- report_path: {absolute path}
- experiment_plan_path: {absolute path}
- feature_branch: {branch}
- base_branch: {branch}
- experiment_type: {type}
- status_badge: {CONCLUSIVE_POSITIVE | CONCLUSIVE_NEGATIVE | INCONCLUSIVE | FAILED}

## Recommendation

{recommendation from Step 3}

## Results Summary

{results_summary from Step 6}

## Hypothesis Table

| | Hypothesis |
|---|---|
| H0 | {H0} |
| H1 | {H1} |

## Metrics Table

| Metric | Unit | Threshold |
|--------|------|-----------|
{DV rows}

## Methodology

{condensed from plan's Methodology section}

## Selected Lenses

{comma-separated slugs}

## Lens Context Paths

{comma-separated absolute paths}
```

Store the absolute path as `prep_file_path`.

---

## Output

Emit these tokens as **literal plain text** (no markdown formatting on the token names)
before `%%ORDER_UP%%`:

```
prep_path = /absolute/path/{{AUTOSKILLIT_TEMP}}/prepare-research-pr/pr_prep_{ts}.md
selected_lenses = fair-comparison,estimand-clarity
lens_context_paths = /abs/ctx_fair-comparison_{ts}.md,/abs/ctx_estimand-clarity_{ts}.md
%%ORDER_UP%%
```

Where:
- `selected_lenses` is a comma-separated list of lens slugs (no spaces)
- `lens_context_paths` is a comma-separated list of absolute context file paths in the
  same order as `selected_lenses`
