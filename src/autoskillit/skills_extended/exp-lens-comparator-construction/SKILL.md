---
name: exp-lens-comparator-construction
categories:
- exp-lens
uses_capabilities: []
activate_deps:
- mermaid
description: Create Comparator Construction experimental design analysis assessing whether baselines and controls are fair
  and relevant. Counterfactual lens answering "Is the comparator fair and relevant?"
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: echo 'Comparator Construction Lens - Analyzing baseline fairness...'
      once: true
semantic_version: 1
semantic_requirements:
  sibling_skills:
  - name: exp-lens-estimand-clarity
  - name: exp-lens-fair-comparison
  - name: make-experiment-diag
  - name: mermaid
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
    for_each: design_dimensions
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
---

# Comparator Construction Experimental Design Lens

> **Preflight:** Before acting on any `exploration-vector` directive below, call `enable_exploration` to establish read-only broker authority for this session; the vectors below assume broker access has already been granted.

**Philosophical Mode:** Counterfactual
**Primary Question:** "Is the comparator fair and relevant?"
**Focus:** Baseline Choice, Control Realism, Version Matching, Effort Symmetry, Baseline Drift

## Arguments

`/autoskillit:exp-lens-comparator-construction [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Benchmark comparisons where baseline quality is questioned
- Ablation studies needing fair controls
- Claims of improvement over prior work
- User invokes `/autoskillit:exp-lens-comparator-construction` or `/autoskillit:make-experiment-diag comparator`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code or experiment files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Accept at face value that baselines received symmetric treatment
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-comparator-construction/`
- Detach child delegations instead of joining them (joining every child is required)
- Run exploration leaves in the background
- Start independent child delegations sequentially

**ALWAYS:**
- Build a fairness matrix covering all treatment-vs-comparator pairs
- Check for confounding differences in implementation, tuning, data access, and compute
- Assess whether each comparator is the best available alternative at the time of the experiment
- Identify temporal drift in baseline relevance
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Start all independent child delegations before awaiting any result to maximize concurrency
- Use the registered exploration roles for all repository reads
- Dispatch every exploration vector below through the deterministic router
- Route semantic code, symbol, and data-control-flow handoffs to `semantic-code-navigator` and bounded configuration, data, fixture, manifest, generated-artifact, reproduction, test, and pre-existing revision-scoped artifact handoffs to `repository-impact-profiler` through the parent-owned plan
- Wait for every exploration result before building the comparator inventory, evaluating fairness, or creating the diagram
- Retain parent authority over comparator relevance and fairness judgments, Mermaid generation, and output writing
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-comparator-construction/exp_diag_comparator_construction_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-comparator-construction/exp_diag_comparator_construction_{...}.md
  ```

---

## Analysis Workflow

### Step 0: Parse optional arguments

If positional arg 1 (context_path) is provided and the file exists, read it to obtain
IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria. If positional
arg 2 (experiment_plan_path) is provided and exists, read the experiment plan for full
methodology. Use this structured context as the foundation for Steps 1-5; skip the CWD
exploration for these fields if the context file supplies them.

<!-- autoskillit:exploration-vector id="missing-context-fields" -->
After the parent parses the optional context and experiment plan, dispatch repository retrieval only for required fields still absent. Never rediscover or override a supplied complete field. If no fields remain missing, report this vector not applicable and perform no search. If scoped evidence is absent or unrelated, report the field unavailable or unrelated without widening scope, inferring meaning, or importing or executing target code, tests, experiments, models, or benchmarks.
<!-- /autoskillit:exploration-vector -->

### Step 1: Launch the Routed Exploration Vectors (SINGLE MESSAGE)

Dispatch all ready, scope-disjoint vectors through the deterministic router in a single message before awaiting any result. Do not iterate across multiple turns.

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Dispatch every authored vector below under their registered role policies. Mixed code and declarative evidence remains one parent-owned plan; bounded role handoffs return to the originating vector and do not add graph dependencies.

<!-- autoskillit:exploration-vector id="baseline-control-definitions" -->
1. **Baseline/Control Definitions** — Find what the proposed method is compared against through `baseline`, `control`, `comparison`, `prior`, `state-of-the-art`, `vanilla`, `default`, and `reference` evidence. Route semantic baseline definitions through the parent to the navigator.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="implementation-parity" -->
2. **Implementation Parity** — Find whether baselines receive equal engineering effort through `reproduce`, `reimplement`, `original`, `paper`, `author`, `tuned`, `optimized`, and `hyperparameter` evidence. Route semantic implementation symbols and call paths through the parent to the navigator.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="version-environment-match" -->
3. **Version & Environment Match** — Find whether baselines use the same software and hardware environment through `version`, `library`, `framework`, `gpu`, `hardware`, `environment`, and `checkpoint` evidence.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="tuning-protocol-symmetry" -->
4. **Tuning Protocol Symmetry** — Find whether hyperparameter tuning is symmetric through `tune`, `search`, `grid`, `optuna`, `sweep`, `budget`, `trials`, and `epochs` evidence.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="temporal-baseline-drift" -->
5. **Temporal Baseline Drift** — Find whether baselines have been updated or are stale through `date`, `published`, `year`, `updated`, `latest`, `deprecated`, and `legacy` evidence.
<!-- /autoskillit:exploration-vector -->

### Step 2: Build the Comparator Inventory

For each comparator, assess:
1. Is it the best available alternative?
2. Is it given equal engineering effort?
3. Is it run in the same environment?
4. Is the tuning budget symmetric?
5. Has it drifted since originally published?

### Step 3: Construct the Fairness Matrix

**CRITICAL — Analyze Counterfactual Quality:**
For each treatment-vs-comparator pair:
- Does the comparison isolate the intended factor?
- Are there confounding differences in implementation, tuning, data access, or compute?

Build a fairness matrix with rows = comparators, columns = fairness dimensions.

### Step 4: Create the Optional Comparison Diagram

If a diagram adds value, create a simplified flowchart. This is OPTIONAL for this hybrid lens — the tables are the primary output.

**Direction:** `LR` (treatment and comparator flow in parallel toward evaluation)

**Subgraphs:** "PROPOSED METHOD", "COMPARATOR(S)", "SHARED EVALUATION"

**Node Styling:**
- `cli` class: proposed method nodes
- `phase` class: comparator method nodes
- `handler` class: shared evaluation pipeline nodes
- `output` class: results nodes
- `gap` class: asymmetries flagged
- `detector` class: parity checks

### Step 5: Write Output

Write the analysis to: `{{AUTOSKILLIT_TEMP}}/exp-lens-comparator-construction/exp_diag_comparator_construction_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

---

## Output Template

```markdown
# Comparator Construction Analysis: {Experiment Name}

**Lens:** Comparator Construction (Counterfactual)
**Question:** Is the comparator fair and relevant?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}

## Comparator Inventory

| Comparator | Source | Reimplemented? | Same Environment? | Same Tuning Budget? |
|------------|--------|---------------|-------------------|---------------------|
| {name} | {paper/repo} | Yes / No / Partial | Yes / No | Yes / No / Unknown |

## Fairness Matrix

| Comparator | Best Available? | Equal Effort? | Same Env? | Symmetric Tuning? | Temporally Current? |
|------------|----------------|--------------|-----------|-------------------|---------------------|
| {name} | Yes / No | Yes / No | Yes / No | Yes / No | Yes / No |

## Comparison Diagram (Optional)

```mermaid
%%{init: {'flowchart': {'nodeSpacing': 50, 'rankSpacing': 60, 'curve': 'basis'}}}%%
flowchart LR
    %% CLASS DEFINITIONS %%
    classDef cli fill:#1a237e,stroke:#7986cb,stroke-width:2px,color:#fff;
    classDef stateNode fill:#004d40,stroke:#4db6ac,stroke-width:2px,color:#fff;
    classDef handler fill:#e65100,stroke:#ffb74d,stroke-width:2px,color:#fff;
    classDef phase fill:#6a1b9a,stroke:#ba68c8,stroke-width:2px,color:#fff;
    classDef newComponent fill:#2e7d32,stroke:#81c784,stroke-width:2px,color:#fff;
    classDef output fill:#00695c,stroke:#4db6ac,stroke-width:2px,color:#fff;
    classDef detector fill:#b71c1c,stroke:#ef5350,stroke-width:2px,color:#fff;
    classDef gap fill:#ff6f00,stroke:#ffa726,stroke-width:2px,color:#000;
    classDef integration fill:#c62828,stroke:#ef9a9a,stroke-width:2px,color:#fff;

    subgraph Proposed ["PROPOSED METHOD"]
        METHOD["Proposed Method<br/>━━━━━━━━━━<br/>{method name}"]
    end

    subgraph Comparators ["COMPARATOR(S)"]
        COMP1["Comparator 1<br/>━━━━━━━━━━<br/>{name}"]
        COMP2["Comparator 2<br/>━━━━━━━━━━<br/>{name}"]
    end

    subgraph Evaluation ["SHARED EVALUATION"]
        EVAL["Evaluation Pipeline<br/>━━━━━━━━━━<br/>{dataset/benchmark}"]
        RESULTS["Results<br/>━━━━━━━━━━<br/>{metrics reported}"]
        PARITY["Parity Check<br/>━━━━━━━━━━<br/>{asymmetry found}"]
        ASYM["Asymmetry<br/>━━━━━━━━━━<br/>{description}"]
    end

    METHOD -->|"evaluated on"| EVAL
    COMP1 -->|"evaluated on"| EVAL
    COMP2 -->|"evaluated on"| EVAL
    EVAL --> RESULTS
    RESULTS --> PARITY
    PARITY -.->|"flagged"| ASYM

    class METHOD cli;
    class COMP1,COMP2 phase;
    class EVAL handler;
    class RESULTS output;
    class PARITY detector;
    class ASYM gap;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Blue | Proposed Method | The method being evaluated |
| Purple | Comparators | Baselines and controls |
| Orange | Evaluation | Shared evaluation pipeline |
| Dark Teal | Results | Reported outcomes |
| Red | Parity Checks | Fairness verification points |
| Yellow | Asymmetries | Flagged unfair differences |

## Asymmetry Register

| # | Asymmetry | Affects | Impact Assessment | Remediation |
|---|-----------|---------|-------------------|-------------|
| 1 | {description} | {comparator(s)} | High / Medium / Low | {how to fix} |

## Recommendations

1. {Most critical fairness fix — e.g., retune baseline with same budget}
2. {Version alignment or environment standardization needed}
3. {Additional comparator that should be included}
```

---

## Pre-Diagram Checklist

Before creating the diagram, verify:

- [ ] LOADED `/autoskillit:mermaid` skill using the Skill tool
- [ ] Using ONLY classDef styles from the mermaid skill (no invented colors)
- [ ] Diagram will include a color legend table

---

## Related Skills

- `/autoskillit:make-experiment-diag` - Parent skill for lens selection
- `/autoskillit:mermaid` - MUST BE LOADED before creating diagram
- `/autoskillit:exp-lens-estimand-clarity` - For clarifying what the comparison is measuring
- `/autoskillit:exp-lens-fair-comparison` - For deeper analysis of evaluation protocol fairness
