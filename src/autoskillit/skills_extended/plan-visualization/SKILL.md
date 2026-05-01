---
name: plan-visualization
categories: [research, vis-lens]
description: >
  Orchestrates 2–4 vis-lens skills in parallel to produce a figure inventory
  (visualization-plan.md) and a report-placement outline (report-plan.md).
  Runs after design review GO, before worktree creation.
---

# Plan Visualization Skill

Reads the finalized experiment plan, selects 2–4 vis-lens skills via three-tier
logic, runs them in parallel, resolves conflicts across their `yaml:figure-spec`
outputs, and synthesizes a complete visualization plan.

## When to Use

- As the `plan_visualization` step of the `research` recipe, after `review_design`
  GO and before `create_worktree`

## Arguments

```
/autoskillit:plan-visualization {source_dir} {experiment_plan_path} {scope_report_path}
```

- `{source_dir}` — Absolute path to the source repo (the CWD before worktree creation)
- `{experiment_plan_path}` — Absolute path to the finalized experiment plan markdown
- `{scope_report_path}` — Absolute path to the scope report (may be empty string if absent)

## Critical Constraints

**NEVER:**
- Select fewer than 2 or more than 4 lenses
- Skip vis-lens-always-on (it is always Tier A)
- Run vis-lens calls across multiple assistant messages — all selected lens calls must
  appear in a SINGLE assistant message to execute in parallel
- Write outputs outside `{{AUTOSKILLIT_TEMP}}/plan-visualization/`
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write a vis-lens context file for each selected lens before invoking it
- Log every conflict resolution decision in the Conflict Resolution Log table
- Emit `visualization_plan_path` and `report_plan_path` tokens as your final output

## Workflow

### Step 0 — Parse Arguments

Extract `source_dir`, `experiment_plan_path`, and `scope_report_path` from arguments.
Read the experiment plan at `experiment_plan_path`.

Extract the following fields (use sensible defaults if absent):
- `experiment_type` — string (e.g., "benchmark", "ablation", "correlation")
- `training_curves` — boolean (default: false)
- `num_DVs` — integer count of dependent variables (default: 1)
- `comparative` — boolean (true if multiple conditions compared head-to-head)
- `DV_types` — list of DV type strings (e.g., ["accuracy", "temporal", "latency"])
- `num_conditions` — integer count of experimental conditions (default: 1)
- `target_domain` — string (e.g., "nlp", "cv", "rl", "general")

### Step 1 — Three-Tier Lens Selection

Build `selected_lenses` (list of 2–4 vis-lens skill slugs):

**Tier A (always selected, mandatory):**
- `vis-lens-always-on`

**Tier B (select 1–2 based on experiment_type and override rules):**

Override rules (checked first, in priority order):
1. If `training_curves == true` → include `vis-lens-temporal`
2. If `num_DVs >= 6 AND comparative == true` → include `vis-lens-multi-compare`
3. If `DV_types` contains `"temporal"` → include `vis-lens-temporal` (if not already)
4. If `num_conditions >= 8` → include `vis-lens-multi-compare` (if not already)

Experiment-type table (use when no override fires or to fill second Tier-B slot):
| experiment_type | Primary lens | Secondary lens (optional) |
|---|---|---|
| benchmark | vis-lens-chart-select | vis-lens-uncertainty |
| ablation | vis-lens-multi-compare | vis-lens-chart-select |
| correlation | vis-lens-chart-select | vis-lens-figure-table |
| regression | vis-lens-temporal | vis-lens-uncertainty |
| classification | vis-lens-chart-select | vis-lens-uncertainty |
| (default) | vis-lens-chart-select | — |

Cap Tier B at 2 lenses total (overrides count toward this cap).

**Tier C (0–1 based on target_domain):**
| target_domain | Lens |
|---|---|
| nlp | vis-lens-domain-norms |
| cv | vis-lens-domain-norms |
| rl | vis-lens-temporal |
| (others / general) | — (skip Tier C) |

Only add Tier C lens if it is not already in Tier A or Tier B.

**Enforcement:** Total must be 2–4. If total < 2, add `vis-lens-chart-select`. If total
> 4, drop the last Tier C lens, then last Tier B secondary.

### Step 2 — Write Vis-Lens Context Files

For each lens in `selected_lenses`, write a context file:

Path: `{{AUTOSKILLIT_TEMP}}/plan-visualization/vis_ctx_{slug}_{YYYY-MM-DD_HHMMSS}.md`

Template for each context file:
```
# Vis-Lens Context: {slug}

## Experiment Summary
{1–3 sentence description of the experiment from the plan}

## Data Shape
- Dependent Variables ({num_DVs} total): {DV names and types}
- Independent Variables: {IV names, levels, and ranges}
- Conditions: {num_conditions} conditions
- Replication: {n_seeds or n_trials if available}

## DV Specification
{For each DV: name, type (continuous/discrete/temporal), unit, expected range}

## IV Specification
{For each IV: name, type, levels (for categorical) or range (for continuous)}

## Comparison Structure
- Comparative: {true/false}
- Head-to-head pairs: {list if applicable}
- Factorial interactions: {list if applicable}

## Expected Data Outputs
{List the files or data structures the experiment will produce, from the plan's
data_manifest or results/ section if available}
```

### Step 3 — Run Vis-Lens Skills in Parallel

In a **single assistant message**, invoke all `selected_lenses` as slash commands:

```
/autoskillit:vis-lens-{slug1} {source_dir} {vis_ctx_path_for_slug1}
/autoskillit:vis-lens-{slug2} {source_dir} {vis_ctx_path_for_slug2}
...
```

Wait for all lens outputs. Read each lens's output file (the `yaml:figure-spec` blocks
within each lens's output markdown).

**Empty plan handling:** If `vis-lens-always-on` returns `SKIP: no_figures_needed`,
record zero figures and proceed to Step 4 with an empty figure list.

### Step 4 — Resolve Conflicts

For each figure-spec block where two lenses disagree on chart type, color encoding,
or layout, apply the conflict resolution hierarchy:

```
accessibility > anti-pattern > domain-norms > chart-select
```

Resolution rules:
- `accessibility` (from `vis-lens-always-on` or `vis-lens-color-access`) wins over all
- `anti-pattern` findings (from `vis-lens-antipattern` or always-on pass 1) override
  chart-select and domain-norms recommendations
- `domain-norms` (from `vis-lens-domain-norms`) overrides `chart-select`
- `chart-select` (from `vis-lens-chart-select`) is the lowest priority

Every resolution must be logged as a row in the Conflict Resolution Log table.

### Step 5 — Write visualization-plan.md

Path: `{{AUTOSKILLIT_TEMP}}/plan-visualization/visualization-plan.md`

Content structure:
```markdown
# Visualization Plan

## Figure Inventory

| Fig ID | Title | Lens Source | Chart Type | Data Source | Priority |
|--------|-------|-------------|------------|-------------|----------|
| fig-1  | ...   | ...         | ...        | ...         | P0/P1/P2 |

## Figure Specifications

{For each figure: paste the yaml:figure-spec block from the winning lens}

## Code Allocation Hints

{For each figure: note which module/file the plotting script should live in,
e.g., `research/{slug}/scripts/fig1_training_curves.py`}

## Conflict Resolution Log

| Fig ID | Dimension | Lens A | Lens A Rec | Lens B | Lens B Rec | Winner | Reason |
|--------|-----------|--------|------------|--------|------------|--------|--------|
```

### Step 6 — Write report-plan.md

Path: `{{AUTOSKILLIT_TEMP}}/plan-visualization/report-plan.md`

Content structure:
```markdown
# Report Plan

## Section Outline

| Report Section | Figure IDs | Notes |
|---|---|---|
| Executive Summary | — | no figures in summary |
| Results | fig-1, fig-2 | ... |
| Analysis | fig-3 | ... |
| Appendix | all | full captions |
```

### Step 7 — Emit Structured Tokens

```
visualization_plan_path = {absolute_path_to_visualization-plan.md}
report_plan_path = {absolute_path_to_report-plan.md}
```
