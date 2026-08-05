---
name: exp-lens-error-budget
categories:
- exp-lens
uses_capabilities: []
activate_deps:
- mermaid
description: Analyze statistical error budget showing Type I/II errors, power, minimum detectable effect, multiplicity corrections,
  and sequential monitoring. Statistical lens answering "Are error risks sized and controlled?"
exploration_vectors:
  - id: missing-context-fields
    disposition: migrated
    rationale: Repository impact evidence retrieves only IV/DV, hypothesis, control, and success-criterion fields absent from supplied context while the parent preserves statistical interpretation.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-error-budget-missing-context-fields
    frontier_item_id: exp-lens-error-budget-missing-context-fields-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: sample-size-power
    disposition: migrated
    rationale: Repository impact evidence inventories sample-size, power, effect-size, and minimum-detectable-effect declarations while the parent evaluates their adequacy.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-error-budget-sample-size-power
    frontier_item_id: exp-lens-error-budget-sample-size-power-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: multiple-comparisons
    disposition: migrated
    rationale: Semantic navigation traces statistical-test and correction symbols and call paths while the parent routes declarative correction artifacts to the profiler and evaluates multiplicity.
    applicability: always
    role: semantic-code-navigator
    profile: auto
    relationship_classes: [defines, calls, references]
    task_id: exp-lens-error-budget-multiple-comparisons
    frontier_item_id: exp-lens-error-budget-multiple-comparisons-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: sequential-analysis
    disposition: migrated
    rationale: Semantic navigation traces interim analysis, early stopping, alpha spending, peeking, and monitoring control flow while the parent evaluates error control.
    applicability: always
    role: semantic-code-navigator
    profile: auto
    relationship_classes: [defines, calls, references]
    task_id: exp-lens-error-budget-sequential-analysis
    frontier_item_id: exp-lens-error-budget-sequential-analysis-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: decision-thresholds
    disposition: migrated
    rationale: Repository impact evidence inventories alpha levels, p-value thresholds, significance declarations, and decision-rule artifacts while the parent judges appropriateness.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-error-budget-decision-thresholds
    frontier_item_id: exp-lens-error-budget-decision-thresholds-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: effect-size-context
    disposition: migrated
    rationale: Repository impact evidence inventories effect-size and practical-significance declarations and reports while the parent determines substantive meaning.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-error-budget-effect-size-context
    frontier_item_id: exp-lens-error-budget-effect-size-context-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: echo 'Error Budget Lens - Analyzing statistical error risks...'
      once: true
semantic_version: 1
semantic_requirements:
  sibling_skills:
  - name: exp-lens-severity-testing
  - name: exp-lens-variance-stability
  - name: make-experiment-diag
  - name: mermaid
---

# Error Budget Experimental Design Lens

**Philosophical Mode:** Statistical
**Primary Question:** "Are error risks sized and controlled?"
**Focus:** Type I/II Errors, Power, Minimum Detectable Effect, Multiplicity, Sequential Monitoring

## Arguments

`/autoskillit:exp-lens-error-budget [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Need to verify statistical power before running an experiment
- Multiple comparisons are performed without a stated correction strategy
- Sequential testing or interim analysis is in use without defined stopping rules
- User invokes `/autoskillit:exp-lens-error-budget` or `/autoskillit:make-experiment-diag error`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Accept default alpha=0.05 without checking whether it is appropriate for the decision context
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-error-budget/`
- Detach child delegations instead of joining them (joining every child is required)
- Run exploration leaves in the background
- Start independent child delegations sequentially

**ALWAYS:**
- Enumerate every statistical test and account for its error contribution
- Distinguish per-test error rates from family-wise error rates
- Flag any sequential peeking without a formal stopping rule as a critical defect
- Evaluate whether the minimum detectable effect is practically meaningful, not just statistically chosen
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Start all independent child delegations before awaiting any result to maximize concurrency
- Use the registered exploration roles for all repository reads
- Dispatch exactly 6 exploration vectors through the deterministic router
- Route semantic code, symbol, and data-control-flow handoffs to `semantic-code-navigator` and bounded configuration, data, fixture, manifest, generated-artifact, reproduction, test, and pre-existing revision-scoped artifact handoffs to `repository-impact-profiler` through the parent-owned plan
- Wait for every exploration result before building the error budget, evaluating statistical risk, or creating the diagram
- Retain parent authority over statistical error, power, and practical-significance judgments, Mermaid generation, and output writing
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-error-budget/exp_diag_error_budget_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-error-budget/exp_diag_error_budget_{...}.md
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

### Step 1: Launch 5 Routed Exploration Vectors (SINGLE MESSAGE)

Dispatch all ready, scope-disjoint vectors through the deterministic router in a single message before awaiting any result. Do not iterate across multiple turns.

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Dispatch exactly these five authored vectors under their registered role policies. Mixed code and declarative evidence remains one parent-owned plan; bounded role handoffs return to the originating vector and do not add graph dependencies.

<!-- autoskillit:exploration-vector id="sample-size-power" -->
1. **Sample Size & Power** — Find power calculations or sample-size justifications through `power`, `sample_size`, `n_samples`, `effect_size`, `minimum_detectable`, and `mde` evidence.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="multiple-comparisons" -->
2. **Multiple Comparisons** — Find all statistical tests performed and correction strategies through `bonferroni`, `fdr`, `holm`, `bh`, `correction`, `multiple`, `comparisons`, and `tests` definitions and calls. Route bounded correction configuration and reports through the parent to the profiler.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="sequential-analysis" -->
3. **Sequential Analysis** — Find interim analyses, stopping rules, and sequential monitoring through `interim`, `early_stopping`, `sequential`, `alpha_spending`, `peek`, and `monitor` control flow. Route bounded stopping configuration through the parent to the profiler.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="decision-thresholds" -->
4. **Decision Thresholds** — Find significance thresholds and decision rules through `alpha`, `p_value`, `threshold`, `significance`, `reject`, `null`, and `hypothesis` evidence. Route semantic decision control flow through the parent to the navigator.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="effect-size-context" -->
5. **Effect Size Context** — Find practical significance alongside statistical significance through `effect_size`, `cohen`, `practical`, `meaningful`, `magnitude`, and `difference` evidence.
<!-- /autoskillit:exploration-vector -->

### Step 2: Build the Error Budget

For each statistical claim:
1. What is the per-test Type I error rate?
2. What is the family-wise Type I error rate?
3. What is the power (1 - Type II error)?
4. What is the minimum detectable effect?
5. Is sequential monitoring in use, and if so, what stopping rule is defined?
6. Is the chosen alpha appropriate for the decision context?

### Step 3: Analyze Error Allocation

For each test, rate alignment as: ALIGNED / CONVENTIONAL / MISALIGNED

### Step 4: Create Optional Decision-Flow Diagram

If a diagram adds value, show Data → Tests → Thresholds → Conclusions, with labeled error rates.

### Step 5: Write Output

Write the analysis to: `{{AUTOSKILLIT_TEMP}}/exp-lens-error-budget/exp_diag_error_budget_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)


## Output Template

```markdown
# Error Budget Analysis: {Experiment Name}

**Lens:** Error Budget (Statistical)
**Question:** Are error risks sized and controlled?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}

## Error Budget

| Test | α (per-test) | Family-wise α | Power (1-β) | MDE | Sequential Rule | Alignment |
|------|-------------|---------------|-------------|-----|-----------------|-----------|
| {test} | {α} | {α_fw} | {power} | {mde} | {rule or N/A} | {ALIGNED/CONVENTIONAL/MISALIGNED} |

## Decision-Flow Diagram

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

    subgraph Data ["DATA"]
        SRC["Dataset<br/>━━━━━━━━━━<br/>N observations"]
    end

    subgraph Tests ["TESTS"]
        T1["Statistical Test<br/>━━━━━━━━━━<br/>α = {value}"]
        MULTI["Multiplicity Correction<br/>━━━━━━━━━━<br/>Family-wise control"]
    end

    subgraph Thresholds ["THRESHOLDS"]
        ALPHA["α Threshold<br/>━━━━━━━━━━<br/>Significance level"]
        POWER["Power Gate<br/>━━━━━━━━━━<br/>1-β = {value}"]
        SEQ["Sequential Rule<br/>━━━━━━━━━━<br/>Monitoring boundary"]
    end

    subgraph Conclusions ["CONCLUSIONS"]
        RESULT["Conclusion<br/>━━━━━━━━━━<br/>Decision"]
    end

    %% DECISION FLOWS %%
    SRC -->|"input"| T1
    T1 -->|"adjusted"| MULTI
    MULTI -->|"p-value"| ALPHA
    T1 -->|"effect size"| POWER
    T1 -->|"interim"| SEQ
    ALPHA -->|"decision"| RESULT
    POWER -->|"adequacy"| RESULT
    SEQ -->|"boundary"| RESULT

    %% CLASS ASSIGNMENTS %%
    class SRC cli;
    class T1 handler;
    class MULTI phase;
    class ALPHA detector;
    class POWER stateNode;
    class SEQ gap;
    class RESULT output;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Blue | Data | Input datasets |
| Orange | Tests | Statistical tests performed |
| Purple | Multiplicity | Multiple comparison corrections |
| Red | α Threshold | Significance decision thresholds |
| Teal | Power | Statistical power assessments |
| Amber | Sequential | Sequential monitoring rules |
| Dark Teal | Conclusions | Final statistical decisions |

## Recommendations

- {Recommendation 1}
- {Recommendation 2}
```

---

## Pre-Diagram Checklist



Before creating the diagram, verify:

- [ ] LOADED `/autoskillit:mermaid` skill using the Skill tool
- [ ] Using ONLY classDef styles from the mermaid skill (no invented colors)
- [ ] Diagram will include a color legend table

---

## Related Skills

- `/autoskillit:make-experiment-diag` - Parent skill
- `/autoskillit:mermaid` - MUST BE LOADED before creating diagram
- `/autoskillit:exp-lens-severity-testing`
- `/autoskillit:exp-lens-variance-stability`
