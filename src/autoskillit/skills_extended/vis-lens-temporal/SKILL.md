---
name: vis-lens-temporal
categories:
- vis-lens
uses_capabilities: []
activate_deps:
- mermaid
description: Create Temporal Dynamics visualization planning spec showing axis scaling (linear vs log), smoothing disclosure,
  epoch/step alignment, run aggregation (mean + variance bands), early-stopping markers, and wall-clock vs step-count x-axis.
  Temporal lens answering "Are training dynamics shown clearly and honestly?"
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: echo 'Temporal Lens - Analyzing training curve representation...'
      once: true
semantic_version: 1
semantic_requirements:
  sibling_skills:
  - name: mermaid
  - name: plan-visualization
  - name: vis-lens-multi-compare
  - name: vis-lens-story-arc
  - name: vis-lens-uncertainty
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
    for_each: vis_checks
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
---

# Temporal Dynamics Visualization Lens

**Philosophical Mode:** Temporal
**Primary Question:** "Are training dynamics shown clearly and honestly?"
**Focus:** Axis Scaling (linear vs log), Smoothing Disclosure, Epoch/Step Alignment,
           Run Aggregation (mean + variance bands), Early-Stopping Markers,
           Wall-Clock vs Step-Count X-Axis

## Arguments

`/autoskillit:vis-lens-temporal [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Reviewing training curves, learning curves, or any metric-vs-step/epoch plots
- Checking whether x-axis units are consistent across compared runs
- Evaluating whether smoothing is disclosed and appropriate
- Planning multi-run aggregation with variance bands
- User invokes `/autoskillit:vis-lens-temporal`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Create files outside `{{AUTOSKILLIT_TEMP}}/vis-lens-temporal/`
- Omit the CRITICAL flag when n_seeds == 1 for training curves — single-seed variance is unquantifiable
- Apply smoothing without disclosing the smoothing window or method
- Mix epoch-count and step-count x-axes on the same multi-run comparison without alignment
- Import or execute target code, tests, experiments, models, benchmarks, notebooks, or plotting workflows to gather evidence

**ALWAYS:**
- CRITICAL: if `n_seeds == 1` for any training curve, flag as **CRITICAL** — single-seed training curves cannot demonstrate stability or convergence robustness
- Disclose smoothing: state the EMA α or window size in the figure caption or axis label
- When comparing runs with different batch sizes or learning rate schedules, align on
  wall-clock time OR total gradient steps (not raw epochs), and document the choice
- Use log-scale y-axis when loss spans more than one order of magnitude
- Mark early-stopping epoch/step as a vertical dashed line with label
- Use the registered exploration roles for all repository reads
- Route the missing-context vector only for fields absent after direct caller-context parsing, and dispatch the 5 repo-local temporal inventory vectors through the deterministic router
- Allow parent-boundary handoff between declarative or generated-artifact evidence and semantic code navigation without creating extra vectors
- Keep external availability, licensing, and network checks lens-owned and outside native exploration
- Wait for every applicable exploration result before judging scale, smoothing, seed sufficiency, alignment, or early-stopping disclosure, emitting figure specifications, or creating the diagram
- Retain parent authority over critical and warning classifications, axis and alignment decisions, disclosure judgment, figure-spec synthesis, and diagram creation
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool — this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Write output to `{{AUTOSKILLIT_TEMP}}/vis-lens-temporal/vis_spec_temporal_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/vis-lens-temporal/vis_spec_temporal_{...}.md
  ```

---

## Analysis Workflow

### Step 0: Parse optional arguments

If positional arg 1 (context_path) is provided and the file exists, read it to obtain
IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria. If positional
arg 2 (experiment_plan_path) is provided and exists, read the experiment plan for full
methodology. Use this structured context as the foundation for Steps 1–4; skip the CWD
exploration for these fields if the context file supplies them.

<!-- autoskillit:exploration-vector id="missing-context-fields" -->
After the parent parses the optional context and experiment plan, dispatch repository retrieval only for required fields still absent. Never rediscover or override a supplied complete field. If no fields remain missing, report this vector not applicable and perform no search. If scoped evidence is absent or unrelated, report the field unavailable or unrelated without widening scope, inferring meaning, or importing or executing target code, tests, experiments, models, or benchmarks.
<!-- /autoskillit:exploration-vector -->

### Step 1: Inventory Training Curves

Use the supplied experiment plan and context directly. Route only the repo-local inventory below through registered exploration roles.

**Learning and Loss Curves**

<!-- autoskillit:exploration-vector id="learning-loss-curves" -->
Inventory pre-existing learning curves, loss curves, metric-versus-step figure specifications, generated figures, tables, data declarations, tests, and fixtures. Include bounded parent-mediated navigator handoff for plotting symbols and consumers; do not import or execute target code.
<!-- /autoskillit:exploration-vector -->

**Seed Count**

<!-- autoskillit:exploration-vector id="seed-count" -->
Trace seed-count and run-count definitions, seed collections, random-state configuration, training-run construction, and the curve paths they affect. Return code relationships and declared values only.
<!-- /autoskillit:exploration-vector -->

**Smoothing Calls**

<!-- autoskillit:exploration-vector id="smoothing-calls" -->
Trace smoothing definitions and calls, including exponential moving averages, rolling means, filters, window sizes, and smoothing parameters, plus the visualization paths they affect. Return evidence only; the parent determines whether smoothing is disclosed and appropriate.
<!-- /autoskillit:exploration-vector -->

**X-Axis Type**

<!-- autoskillit:exploration-vector id="x-axis-type" -->
Trace epoch-count, step-count, global-step, and wall-clock definitions and plotting references, including axis labels and the multi-run comparisons they affect. Return evidence only; the parent determines the axis type and alignment.
<!-- /autoskillit:exploration-vector -->

**Early Stopping**

<!-- autoskillit:exploration-vector id="early-stopping" -->
Trace early-stopping definitions, configuration, patience and best-epoch references, control flow, and affected plot annotations. Return evidence only; the parent determines whether stopping is disclosed and marked.
<!-- /autoskillit:exploration-vector -->

### Step 2: Determine Axis Scaling

For each loss or metric curve, check the range:
- If loss spans more than one order of magnitude (max/min > 10): recommend log-scale y-axis
- If loss is bounded (e.g., accuracy 0–1): linear scale is acceptable
- Document the recommendation with the detected range

### Step 3: Alignment Check

For all multi-run comparisons:
- Verify that all compared runs use the same x-axis unit (epoch vs step vs time)
- Flag mismatches as WARNING: "Runs use mixed x-axis units — align on gradient steps or wall-clock time"
- Check batch size and learning rate schedule consistency across compared runs

### Step 4: Emit yaml:figure-spec Blocks

For each figure, emit one `yaml:figure-spec` fenced block with the `stat_overlay`
variance band filled in. Then LOAD `/autoskillit:mermaid` and create a temporal flow
diagram showing x-axis unit → scaling choice → smoothing annotation → variance band → verdict.

---

## Output Template

```markdown
# Temporal Dynamics Spec: {System / Experiment Name}

**Lens:** Temporal Dynamics (Temporal)
**Question:** Are training dynamics shown clearly and honestly?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}
**n_seeds detected:** {N}

## Temporal Audit Summary

| Figure | n_seeds | x_axis | y_scale | smoothing | early_stop_marked | Status |
|--------|---------|--------|---------|-----------|-------------------|--------|
| {fig-01} | 1 | epoch | linear | none | no | CRITICAL |
| {fig-02} | 5 | step | log | EMA α=0.9 | yes | OK |

## Figure Specs

```yaml
# yaml:figure-spec — canonical schema (spec_version: "1.0")
figure_id: "fig-02-loss-curve"
figure_title: "Training Loss vs Gradient Steps"
spec_version: "1.0"
chart_type: "line"
chart_type_fallback: "scatter"
perceptual_justification: "Log-scale y-axis spans 2 orders of magnitude; variance band shows run stability."
data_source: "results/loss_curves.csv"
data_mapping:
  x: "global_step"
  y: "train_loss"
  color: "run_id"
  size: ""
  facet: ""
layout:
  width_inches: 6.0
  height_inches: 4.0
  dpi: 300
stat_overlay:
  type: "band"
  measure: "CI95"
  n_seeds: 5
annotations: ["log-scale y; EMA α=0.9 disclosed; early-stop at step 4200"]
anti_patterns: ["ap-missing-variance-band"]
palette: "okabe-ito"
format: "pdf"
target_dpi: 300
library: "matplotlib"
report_section: "Section 3 Training"
image_path: ""
priority: "P1"
placement_tier: "main"
conflicts: []
metadata:
  created_by: "vis-lens-temporal"
  reviewed_by: ""
  last_updated: "{YYYY-MM-DD}"
```

## Temporal Dynamics Diagram

```mermaid
%%{init: {'flowchart': {'nodeSpacing': 50, 'rankSpacing': 60, 'curve': 'basis'}}}%%
flowchart TB
    %% CLASS DEFINITIONS %%
    classDef cli fill:#1a237e,stroke:#7986cb,stroke-width:2px,color:#fff;
    classDef stateNode fill:#004d40,stroke:#4db6ac,stroke-width:2px,color:#fff;
    classDef handler fill:#e65100,stroke:#ffb74d,stroke-width:2px,color:#fff;
    classDef output fill:#00695c,stroke:#4db6ac,stroke-width:2px,color:#fff;
    classDef detector fill:#b71c1c,stroke:#ef5350,stroke-width:2px,color:#fff;

    subgraph XAxis ["X-AXIS UNIT"]
        X1["epoch / step / wall-clock<br/>━━━━━━━━━━<br/>{alignment status}"]
    end

    subgraph Scale ["Y-AXIS SCALING"]
        Y1["linear / log<br/>━━━━━━━━━━<br/>{loss range: {min}–{max}}"]
    end

    subgraph Smooth ["SMOOTHING"]
        S1["none / EMA / rolling<br/>━━━━━━━━━━<br/>{α or window disclosed: yes/no}"]
    end

    subgraph Variance ["VARIANCE BAND"]
        V1["CI95 / SD band<br/>━━━━━━━━━━<br/>n_seeds = {N}"]
    end

    subgraph Verdict ["VERDICT"]
        VD1["{OK / WARNING / CRITICAL}<br/>━━━━━━━━━━<br/>{reason}"]
    end

    X1 --> Y1
    Y1 --> S1
    S1 --> V1
    V1 --> VD1

    class X1 stateNode;
    class Y1 cli;
    class S1 handler;
    class V1 output;
    class VD1 detector;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Teal | X-Axis | Unit choice and alignment status |
| Dark Blue | Y-Axis | Scaling decision based on loss range |
| Orange | Smoothing | Disclosure status |
| Teal | Variance | Band type and seed count |
| Red | Verdict | OK / WARNING / CRITICAL assessment |
```

---

## Pre-Diagram Checklist

Before creating the diagram, verify:

- [ ] LOADED `/autoskillit:mermaid` skill using the Skill tool
- [ ] Using ONLY classDef styles from the mermaid skill (no invented colors)
- [ ] Diagram will include a color legend table
- [ ] Every CRITICAL (n_seeds == 1) training curve is flagged
- [ ] Every smoothing call has its parameters disclosed in the figure spec
- [ ] Early-stopping markers are noted for all curves with early stopping

---

## Related Skills

- `/autoskillit:plan-visualization` - Parent skill for lens selection
- `/autoskillit:mermaid` - MUST BE LOADED before creating diagram
- `/autoskillit:vis-lens-uncertainty` - For statistical uncertainty visualization
- `/autoskillit:vis-lens-story-arc` - For narrative arc and color consistency
- `/autoskillit:vis-lens-multi-compare` - For multi-condition comparison layouts
