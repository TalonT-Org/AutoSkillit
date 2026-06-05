---
name: synthesize-vis-plan
categories: [research, vis-lens]
description: >
  Synthesize step of the vis-lens phoropter: reads captured yaml:figure-spec
  blocks from lens output files, resolves inter-lens conflicts via the
  four-level priority hierarchy, and produces visualization-plan.md,
  report-plan.md, and visualization-plan-trace.md for downstream worktree
  creation.
---

# Synthesize Vis-Plan Skill

Reads captured `yaml:figure-spec` blocks from vis-lens output files, resolves
conflicts across their recommendations using the priority hierarchy, and writes
three output files: a visualization plan with figure inventory, a report
placement outline, and a Tier-C routing trace.

The input lens outputs originate from three selection tiers defined by
`select-vis-lenses`: Tier A (always-on mandatory lenses), Tier B
(experiment-type-selected lenses), and Tier C (methodology-tradition-selected
lens). The priority hierarchy in Step 1 maps directly to these tiers.

## When to Use

- As the `synthesize` step of the vis-lens phoropter in the `research` recipe,
  after the `run_vis_lenses` apply step and before `create_worktree`
- on_success: `create_worktree`
- on_failure: `escalate_stop`

This skill is the vis-lens-specific implementation of phoropter synthesis. It
expects `yaml:figure-spec` blocks as input, applies the fixed four-level
priority hierarchy (accessibility > anti-pattern > methodology-norms >
chart-select), consumes Tier-C routing args sourced from `select-vis-lenses`,
and produces three-file output (visualization-plan.md, report-plan.md,
visualization-plan-trace.md). For family-agnostic synthesis with a configurable
hierarchy and token-based input, use `phoropter-priority-synthesis` instead.

## Arguments

```
/autoskillit:synthesize-vis-plan {source_dir} {experiment_plan_path} {capture_dir} --tier-c-lens={tier_c_lens} --methodology-tradition={methodology_tradition} --disambiguation-rule-applied={disambiguation_rule_applied} --applied-union-rules={applied_union_rules} --precedence-trace={precedence_trace}
```

**Positional arguments:**

- `{source_dir}` — Absolute path to the source repo (the CWD before worktree creation)
- `{experiment_plan_path}` — Absolute path to the finalized experiment plan markdown
- `{capture_dir}` — Absolute path to the directory containing lens output files (vis-lens
  markdown files with `yaml:figure-spec` fenced blocks)

**Tier-C routing fields** (named arguments, passed from the `select-vis-lenses` context):

- `--tier-c-lens` — The Tier-C lens name selected by `select-vis-lenses` (e.g.,
  `vis-lens-methodology-norms` or `null`). Token name: `tier_c_lens`
- `--methodology-tradition` — The primary methodology tradition slug resolved by Tier-C
  routing (e.g., `controlled_intervention` or `null`). Token name: `methodology_tradition`
- `--disambiguation-rule-applied` — The first disambiguation rule name from
  `applied_union_rules` if non-empty, else `null`. Token name: `disambiguation_rule_applied`
- `--applied-union-rules` — Comma-separated list of union rule names accumulated during
  multi-tradition disambiguation, sourced from the methodology-norms context file passed as
  arguments from `select-vis-lenses` (not from recipe context). Token name: `applied_union_rules`
- `--precedence-trace` — The resolution chain string encoding Tier-C routing decisions,
  sourced from the methodology-norms context file passed as arguments from
  `select-vis-lenses` (not from recipe context). Token name: `precedence_trace`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Write outputs outside `{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/`
- Omit any of the three required path tokens (`visualization_plan_path`,
  `report_plan_path`, `visualization_plan_trace_path`)
- Skip a conflict resolution log row — every resolution must produce a row
- Spawn sub-agents or run sub-agents in the background

**ALWAYS:**
- Log every conflict resolution decision as a row in the Conflict Resolution Log table
  with columns: Fig ID, Dimension, Lens A, Lens A Rec, Lens B, Lens B Rec, Winner, Reason
- Emit all three structured path tokens as your final output
- Use `{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/` (relative to the current working directory) for all output paths
- Write all three output files unconditionally — even when the figure list is empty,
  write files with empty tables and zero-row logs

---

## Workflow

### Step 0 — Parse Arguments

Extract positional arguments:
- `source_dir` — the source repository path
- `experiment_plan_path` — path to the experiment plan
- `capture_dir` — directory containing lens output files

Extract Tier-C routing fields from named arguments:
- `tier_c_lens`
- `methodology_tradition`
- `disambiguation_rule_applied`
- `applied_union_rules` — sourced from the methodology-norms context file passed as
  arguments from `select-vis-lenses`, not from recipe context
- `precedence_trace` — sourced from the methodology-norms context file passed as
  arguments from `select-vis-lenses`, not from recipe context

Read all files matching `capture_dir/**/*.md` and extract `yaml:figure-spec` fenced
blocks from each file. Parse each block as YAML to produce a list of figure
specification objects.

A `yaml:figure-spec` block is a fenced code block with info string `yaml:figure-spec`:

```
```yaml:figure-spec
figure_id: fig-01-main-accuracy
figure_title: "Main Accuracy Comparison"
chart_type: grouped_bar
...
`` `
```

**Empty plan handling:** If no `yaml:figure-spec` blocks are found in any lens output
file, record zero figures and proceed to Step 1 with an empty figure list. All three
output files are still written (with empty tables).

### Step 1 — Resolve Conflicts

For each figure-spec block where two lenses disagree on chart type, color encoding,
or layout, apply the conflict resolution priority hierarchy:

```
accessibility > anti-pattern > methodology-norms > chart-select
```

**Resolution Rules:**

| Priority | Source | Wins Over |
|----------|--------|-----------|
| 1 (highest) | `accessibility` (from `vis-lens-always-on` or `vis-lens-color-access`) | all |
| 2 | `anti-pattern` (from `vis-lens-antipattern` or always-on pass 1) | methodology-norms, chart-select |
| 3 | `methodology-norms` (from `vis-lens-methodology-norms`) | chart-select |
| 4 (lowest) | `chart-select` (from `vis-lens-chart-select`) | — |

Every resolution must be logged as a row in the Conflict Resolution Log table:

| Fig ID | Dimension | Lens A | Lens A Rec | Lens B | Lens B Rec | Winner | Reason |
|--------|-----------|--------|------------|--------|------------|--------|--------|

The `Reason` column must reference the priority hierarchy rule that determined the
winner (e.g., "accessibility overrides chart-select per priority hierarchy").

If the figure list is empty or no conflicts exist, the Conflict Resolution Log table
is written with headers only and zero data rows.

### Step 2 — Write visualization-plan.md and report-plan.md

Write both files to `{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/`.

**visualization-plan.md** at `{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/visualization-plan.md`:

```markdown
# Visualization Plan

## Figure Inventory

| Fig ID | Title | Lens Source | Chart Type | Data Source | Priority |
|--------|-------|-------------|------------|-------------|----------|
| fig-1  | ...   | ...         | ...        | ...         | P0/P1/P2 |

## Figure Specifications

{For each figure: paste the resolved yaml:figure-spec block from the winning lens,
applying any overrides from higher-priority lenses}

## Code Allocation Hints

{For each figure: note which module/file the plotting script should live in,
e.g., `research/{slug}/scripts/fig1_training_curves.py`}

## Conflict Resolution Log

| Fig ID | Dimension | Lens A | Lens A Rec | Lens B | Lens B Rec | Winner | Reason |
|--------|-----------|--------|------------|--------|------------|--------|--------|
```

**report-plan.md** at `{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/report-plan.md`:

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

### Step 3 — Write visualization-plan-trace.md

Write the trace file to `{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/visualization-plan-trace.md`.

Populate from the Tier-C routing fields received as skill arguments:

```markdown
# Visualization Plan Trace

## Tier-C Routing Decision

- **tier_c_lens**: {tier_c_lens argument value or null}
- **primary_tradition**: {methodology_tradition argument value or null}
- **disambiguation_rule_applied**: {disambiguation_rule_applied argument value or null}
- **applied_union_rules**: [{applied_union_rules argument value, or empty list}]
- **precedence_trace**: [{precedence_trace argument value, or null}]
```

Note: The `primary_tradition` field in the trace file is populated from the
`methodology_tradition` argument. This mapping preserves compatibility with the
trace format established by the monolithic `plan-visualization` skill.

### Step 4 — Emit Structured Tokens

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
visualization_plan_path = {absolute_path_to_{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/visualization-plan.md}
report_plan_path = {absolute_path_to_{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/report-plan.md}
visualization_plan_trace_path = {absolute_path_to_{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/visualization-plan-trace.md}
```

All three tokens are mandatory — always emit non-null absolute paths. All three
output files are always written (even for empty figure lists), so all three tokens
are always non-null.