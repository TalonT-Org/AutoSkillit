---
name: vis-lens-multi-compare
categories:
- vis-lens
uses_capabilities: []
activate_deps:
- mermaid
description: Create Compositional Layout visualization planning spec showing small-multiples vs overlay decisions, faceting
  strategy (row/col), shared-axis alignment, grouped vs stacked bars, factorial interaction plots, and panel reading order.
  Compositional lens answering "Which layout best reveals the comparison structure?"
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: echo 'Multi-Compare Lens - Analyzing compositional layout strategy...'
      once: true
semantic_version: 1
semantic_requirements:
  sibling_skills:
  - name: mermaid
  - name: plan-visualization
  - name: vis-lens-methodology-norms
  - name: vis-lens-reproducibility
  - name: vis-lens-story-arc
  - name: vis-lens-temporal
---

# Compositional Layout Visualization Lens

**Philosophical Mode:** Compositional
**Primary Question:** "Which layout — small multiples vs overlay — best reveals the comparison?"
**Focus:** Small Multiples vs Overlay, Faceting Strategy (row/col), Shared-Axis Alignment,
           Grouped vs Stacked Bars, Factorial Interaction Plots, Panel Reading Order

## Arguments

`/autoskillit:vis-lens-multi-compare [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Designing layouts for experiments with multiple conditions, factors, or treatments
- Deciding whether to use small multiples or overlapping traces in a figure
- Planning faceted grids for factorial designs (2+ independent variables × 2+ levels)
- Evaluating whether grouped or stacked bars serve the comparison goal
- User invokes `/autoskillit:vis-lens-multi-compare`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Create files outside `{{AUTOSKILLIT_TEMP}}/vis-lens-multi-compare/`
- Use overlapping traces when ≥ 4 conditions are compared — prefer small multiples
- Use stacked bars for comparisons where the baseline shifts — use grouped bars instead
- Import or execute target code, tests, experiments, models, plotting pipelines, or benchmarks
- Let a migrated exploration vector assess visual overlap, choose a layout, assign facets, design the figure, or create the diagram

**ALWAYS:**
- Prefer small multiples over overlays when: num_conditions ≥ 5, OR data series overlap
  heavily, OR the comparison requires individual-panel annotation
- Apply consistent shared-axis limits across all panels in a small-multiples layout
- Enforce left-to-right, top-to-bottom panel reading order matching the experimental
  factor order (main factor changes columns; secondary factor changes rows)
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool — this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Use the registered exploration roles for all repository reads
- Dispatch exactly 3 migrated exploration vectors through the deterministic router
- Keep series-overlap assessment as a retained parent-owned source block
- Route mixed declarative factor evidence through the parent for bounded profiler handoff without creating extra vectors
- Wait for every migrated exploration result before choosing overlay, small multiples, grouped bars, or stacked bars
- Retain parent authority over perceptual overlap, layout, facet, reading-order, and visualization judgments in Steps 1–4
- Write output to `{{AUTOSKILLIT_TEMP}}/vis-lens-multi-compare/vis_spec_multi_compare_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/vis-lens-multi-compare/vis_spec_multi_compare_{...}.md
  ```

---

## Analysis Workflow

### Step 0: Parse optional arguments

If positional arg 1 (context_path) is provided and the file exists, read it to obtain
IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria. If positional
arg 2 (experiment_plan_path) is provided and exists, read the experiment plan for full
methodology. Use this structured context as the foundation for Steps 1–4; skip the CWD
exploration for fields supplied completely by those artifacts.

<!-- autoskillit:exploration-vector id="missing-context-fields" -->
After the parent parses supplied context and experiment-plan arguments, inspect only
existing revision-scoped CWD artifacts for fields that remain absent. Never rediscover
or override supplied complete fields. If no fields are missing, return an explicit
not-applicable result without repository search. If relevant evidence is absent or
unrelated, explicitly report it as unavailable or unrelated without widening scope,
inferring meaning, or importing or executing target code, tests, experiments, models,
or benchmarks.
<!-- /autoskillit:exploration-vector -->

### Step 1: Inventory Conditions and Factors

Dispatch the two authored repository-discovery vectors with the ready fallback vector
through one deterministic router plan before awaiting any result. Supplied context and
experiment-plan content remain direct parent inputs.

**Condition and Factor Count**
<!-- autoskillit:exploration-vector id="condition-factor-definitions" -->
- Trace repository-local condition, treatment-arm, group, and factor definitions and their consumers
- Look for: condition lists, treatment arms, `conditions = [...]`, `groups = [...]`, factor tables
<!-- /autoskillit:exploration-vector -->

The parent counts num_DVs, num_conditions, and num_factors using supplied context and
the collected repository evidence.

**Series Overlap**
<!-- autoskillit:exploration-vector id="series-overlap-assessment" -->
- Assess whether plotting all conditions on a single axes would create heavy visual overlap
- Look for: overlapping confidence bands, dense line clusters, label collisions
<!-- /autoskillit:exploration-vector -->

**Factorial Structure**
<!-- autoskillit:exploration-vector id="factorial-structure" -->
- Detect whether the design is factorial (2+ IVs × 2+ levels each)
- Look for: interaction terms, crossed factors, `factorial`, `grid_search`
<!-- /autoskillit:exploration-vector -->

### Step 2: Apply Small-Multiples vs Overlay Decision Rule

For each figure that shows multi-condition data, determine the layout strategy:

**Overlay (single axes):**
- ≤ 3 conditions, no label collision, primary message is aggregate trend
- Use when: the comparison is a single-axis trend, conditions are well-separated visually

**Small Multiples (faceted grid):**
- ≥ 4 conditions, OR heavy overlap, OR per-panel annotation needed
- OR factorial (2+ IVs × 2+ levels): always use small multiples
- Assign `row_factor` and `col_factor` explicitly

**Stacked vs Grouped Bars:**
- Stacked bars: only when part-to-whole is the story AND baselines are shared
- Grouped bars: when individual comparison matters more than the total

### Step 3: Assign Facet Layout

For small-multiples layouts:
- Assign `col_factor` to the main independent variable (most levels or primary interest)
- Assign `row_factor` to the secondary independent variable
- Set shared x-axis and y-axis limits across all panels
- Document reading order: left-to-right (col_factor levels), top-to-bottom (row_factor levels)

### Step 4: Emit yaml:figure-spec Blocks

For each figure, emit one `yaml:figure-spec` fenced block with `facet` field populated.
Then LOAD `/autoskillit:mermaid` and create a panel-layout schematic diagram (boxes
representing panel grid with row/col labels).

---

## Output Template

```markdown
# Compositional Layout Spec: {System / Experiment Name}

**Lens:** Compositional Layout (Compositional)
**Question:** Which layout — small multiples vs overlay — best reveals the comparison?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}
**num_conditions detected:** {N}

## Layout Decision Summary

| Figure | num_conditions | num_factors | Strategy | row_factor | col_factor |
|--------|---------------|-------------|----------|------------|------------|
| {fig-01} | 6 | 2 | small-multiples | method | dataset |
| {fig-02} | 3 | 1 | overlay | — | — |

## Figure Specs

```yaml
# yaml:figure-spec — canonical schema (spec_version: "1.0")
figure_id: "fig-01-factorial-accuracy"
figure_title: "Accuracy Across Methods × Datasets"
spec_version: "1.0"
chart_type: "line"
chart_type_fallback: "grouped-bar"
perceptual_justification: "Small multiples prevent overlap; shared y-axis enables cross-panel comparison."
data_source: "results/accuracy.csv"
data_mapping:
  x: "epoch"
  y: "accuracy"
  color: "variant"
  size: ""
  facet: "col=dataset, row=method"
layout:
  width_inches: 10.0
  height_inches: 6.0
  dpi: 300
stat_overlay:
  type: "error_bar"
  measure: "CI95"
  n_seeds: 5
annotations: ["shared y-axis; panel grid: 3 cols × 2 rows"]
anti_patterns: ["ap-overplotting"]
palette: "okabe-ito"
format: "pdf"
target_dpi: 300
library: "matplotlib"
report_section: "Section 4 Results"
image_path: ""
priority: "P1"
placement_tier: "main"
conflicts: []
metadata:
  created_by: "vis-lens-multi-compare"
  reviewed_by: ""
  last_updated: "{YYYY-MM-DD}"
```

## Compositional Layout Diagram

```mermaid
%%{init: {'flowchart': {'nodeSpacing': 50, 'rankSpacing': 60, 'curve': 'basis'}}}%%
flowchart TB
    %% CLASS DEFINITIONS %%
    classDef cli fill:#1a237e,stroke:#7986cb,stroke-width:2px,color:#fff;
    classDef stateNode fill:#004d40,stroke:#4db6ac,stroke-width:2px,color:#fff;
    classDef handler fill:#e65100,stroke:#ffb74d,stroke-width:2px,color:#fff;
    classDef output fill:#00695c,stroke:#4db6ac,stroke-width:2px,color:#fff;
    classDef detector fill:#b71c1c,stroke:#ef5350,stroke-width:2px,color:#fff;

    subgraph Conditions ["CONDITION COUNT"]
        C1["num_conditions = {N}<br/>━━━━━━━━━━<br/>{factorial: yes/no}"]
    end

    subgraph Strategy ["LAYOUT STRATEGY"]
        S1["overlay / small-multiples<br/>━━━━━━━━━━<br/>{decision rule applied}"]
    end

    subgraph Facet ["FACET ASSIGNMENT"]
        F1["col_factor = {X}<br/>row_factor = {Y}<br/>━━━━━━━━━━<br/>shared axes: yes/no"]
    end

    subgraph Verdict ["VERDICT"]
        V1["{layout type}<br/>━━━━━━━━━━<br/>{reading order note}"]
    end

    C1 --> S1
    S1 --> F1
    F1 --> V1

    class C1 stateNode;
    class S1 cli;
    class F1 handler;
    class V1 output;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Teal | Condition Count | Number of conditions and factorial structure |
| Dark Blue | Strategy | Overlay vs small-multiples decision |
| Orange | Facet Assignment | Row/col factor and shared-axis configuration |
| Teal | Verdict | Final layout recommendation |
```

---

## Pre-Diagram Checklist

Before creating the diagram, verify:

- [ ] LOADED `/autoskillit:mermaid` skill using the Skill tool
- [ ] Using ONLY classDef styles from the mermaid skill (no invented colors)
- [ ] Diagram will include a color legend table
- [ ] Every figure with ≥ 4 conditions has been assigned a small-multiples layout
- [ ] Every `yaml:figure-spec` has the `facet` field filled (or explicitly empty for overlays)

---

## Related Skills

- `/autoskillit:plan-visualization` - Parent skill for lens selection
- `/autoskillit:mermaid` - MUST BE LOADED before creating diagram
- `/autoskillit:vis-lens-temporal` - For time-series and training curve analysis
- `/autoskillit:vis-lens-story-arc` - For narrative arc and color consistency
- `/autoskillit:vis-lens-methodology-norms` - For field-specific methodology compliance
- `/autoskillit:vis-lens-reproducibility` - For reproducibility and data provenance checks
