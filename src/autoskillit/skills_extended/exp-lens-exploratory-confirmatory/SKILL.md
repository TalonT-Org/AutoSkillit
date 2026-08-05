---
name: exp-lens-exploratory-confirmatory
categories:
- exp-lens
uses_capabilities: []
activate_deps:
- mermaid
description: Assess whether analytic decisions were pre-specified or post-hoc and whether exploratory/confirmatory norms are
  aligned. Boundary lens answering "Is this discovery or test, and are norms aligned?"
exploration_vectors:
  - id: missing-context-fields
    disposition: migrated
    rationale: Repository impact evidence retrieves only IV/DV, hypothesis, control, and success-criterion fields absent from supplied context while the parent preserves boundary interpretation.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-exploratory-confirmatory-missing-context-fields
    frontier_item_id: exp-lens-exploratory-confirmatory-missing-context-fields-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: pre-specified-plans
    disposition: migrated
    rationale: Repository impact evidence inventories preregistrations, analysis plans, hypothesis files, protocols, and specifications while the parent assesses pre-specification.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-exploratory-confirmatory-pre-specified-plans
    frontier_item_id: exp-lens-exploratory-confirmatory-pre-specified-plans-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: analytic-flexibility
    disposition: migrated
    rationale: Semantic navigation traces alternative, option, variant, subset, sensitivity, and robustness branches while the parent counts forking paths and routes declarative variants to the profiler.
    applicability: always
    role: semantic-code-navigator
    profile: auto
    relationship_classes: [defines, calls, references]
    task_id: exp-lens-exploratory-confirmatory-analytic-flexibility
    frontier_item_id: exp-lens-exploratory-confirmatory-analytic-flexibility-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: selective-reporting-signals
    disposition: migrated
    rationale: Repository impact evidence finds excluded, hidden, supplementary, and non-significant result artifacts while the parent evaluates selective reporting.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-exploratory-confirmatory-selective-reporting-signals
    frontier_item_id: exp-lens-exploratory-confirmatory-selective-reporting-signals-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: post-hoc-rationalization
    disposition: migrated
    rationale: Repository impact evidence finds post-hoc language in repository documentation and pre-existing revision-scoped artifacts while the parent determines whether it signals HARKing.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-exploratory-confirmatory-post-hoc-rationalization
    frontier_item_id: exp-lens-exploratory-confirmatory-post-hoc-rationalization-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: exploration-confirmation-separation
    disposition: migrated
    rationale: Repository impact evidence inventories explicit exploratory, pilot, hypothesis-generating, confirmatory, and pre-specified declarations while the parent judges boundary integrity.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [declares, references, affects]
    task_id: exp-lens-exploratory-confirmatory-exploration-confirmation-separation
    frontier_item_id: exp-lens-exploratory-confirmatory-exploration-confirmation-separation-frontier
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
      command: echo 'Exploratory-Confirmatory Lens - Analyzing boundary integrity...'
      once: true
semantic_version: 1
semantic_requirements:
  sibling_skills:
  - name: exp-lens-sensitivity-robustness
  - name: exp-lens-severity-testing
  - name: make-experiment-diag
  - name: mermaid
---

# Exploratory-Confirmatory Experimental Design Lens

**Philosophical Mode:** Boundary
**Primary Question:** "Is this discovery or test, and are norms aligned?"
**Focus:** Pre-specification, Analytic Flexibility, HARKing Detection, Garden of Forking Paths, Transparent Reporting

## Arguments

`/autoskillit:exp-lens-exploratory-confirmatory [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Study mixes exploration and confirmation without clear boundaries
- Post-hoc hypotheses presented as pre-specified
- Many analyses run but only significant ones reported
- User invokes `/autoskillit:exp-lens-exploratory-confirmatory` or `/autoskillit:make-experiment-diag exploratory`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-exploratory-confirmatory/`
- Detach child delegations instead of joining them (joining every child is required)
- Run exploration leaves in the background
- Start independent child delegations sequentially

**ALWAYS:**
- Map the full analytic timeline — what was decided before vs. after seeing data
- Count forking paths honestly — every analysis choice is a potential fork
- Distinguish genuine exploration (hypothesis-generating) from HARKing (hypothesis-after-results)
- Flag absent preregistration as a finding without assuming bad faith
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Start all independent child delegations before awaiting any result to maximize concurrency
- Use the registered exploration roles for all repository reads
- Dispatch exactly 6 exploration vectors through the deterministic router
- Route semantic code, symbol, and data-control-flow handoffs to `semantic-code-navigator` and bounded configuration, data, fixture, manifest, generated-artifact, reproduction, test, and pre-existing revision-scoped artifact handoffs to `repository-impact-profiler` through the parent-owned plan
- Wait for every exploration result before mapping the analytic timeline, evaluating boundary integrity, or creating the diagram
- Retain parent authority over exploratory-confirmatory and HARKing judgments, Mermaid generation, and output writing
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-exploratory-confirmatory/exp_diag_exploratory_confirmatory_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-exploratory-confirmatory/exp_diag_exploratory_confirmatory_{...}.md
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

<!-- autoskillit:exploration-vector id="pre-specified-plans" -->
1. **Pre-specified Plans** — Find preregistration documents, analysis plans, and hypothesis files through `preregister`, `analysis_plan`, `hypothesis`, `registered`, `protocol`, and `spec` evidence.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="analytic-flexibility" -->
2. **Analytic Flexibility** — Find places where multiple analysis paths were possible through `alternatively`, `could_also`, `option`, `variant`, `subset`, `sensitivity`, and `robustness` definitions and control flow. Route bounded configuration variants through the parent to the profiler.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="selective-reporting-signals" -->
3. **Selective Reporting Signals** — Find evidence of selective reporting or cherry-picking through `not_significant`, `excluded`, `not_shown`, `supplementary`, `additional`, and `hidden` artifacts.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="post-hoc-rationalization" -->
4. **Post-Hoc Rationalization** — Find language suggesting post-hoc hypothesis generation through `we_noticed`, `interestingly`, `surprisingly`, `unexpectedly`, and `upon_inspection` evidence.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="exploration-confirmation-separation" -->
5. **Exploration-Confirmation Separation** — Find explicit statements about exploratory versus confirmatory intent through `exploratory`, `pilot`, `hypothesis_generating`, `confirmatory`, and `pre_specified` evidence. Route semantic data-split and analysis-path control flow through the parent to the navigator.
<!-- /autoskillit:exploration-vector -->

### Step 2: Map Analytic Timeline

What was decided before vs. after seeing data? Where is the exploration/confirmation boundary? Count forking paths.

### Step 3: Analyze Boundary Integrity

For every reported result: Was the analysis plan fixed pre-outcome? How many alternatives could have been run? Does reporting distinguish exploratory from confirmatory? Assess survivorship bias.

### Step 4: Create the Diagram (Optional)

**Direction:** LR (time flows left to right). Pre-data decisions → Data collection → Post-data decisions → Reporting

### Step 5: Write Output

Write the output to: `{{AUTOSKILLIT_TEMP}}/exp-lens-exploratory-confirmatory/exp_diag_exploratory_confirmatory_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)


## Output Template

```markdown
# Exploratory-Confirmatory Analysis: {Experiment Name}

**Lens:** Exploratory-Confirmatory (Boundary)
**Question:** Is this discovery or test, and are norms aligned?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}

## Analytic Timeline

| Decision | Pre/Post Data | Forking Paths | HARKing Risk | Verdict |
|----------|---------------|---------------|--------------|---------|
| {decision} | {Pre/Post} | {count} | {High/Medium/Low/None} | {Exploratory/Confirmatory/Mixed} |

## Boundary Diagram

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

    subgraph PreData ["PRE-DATA DECISIONS"]
        HYPO["Hypotheses<br/>━━━━━━━━━━<br/>Pre-registered?"]
        PLAN["Analysis Plan<br/>━━━━━━━━━━<br/>Fixed protocol"]
    end

    subgraph DataCollection ["DATA COLLECTION"]
        DATA["Data<br/>━━━━━━━━━━<br/>Collection method"]
    end

    subgraph PostData ["POST-DATA DECISIONS"]
        FORK["Forking Paths<br/>━━━━━━━━━━<br/>N alternatives"]
        HARK["HARKing Risk<br/>━━━━━━━━━━<br/>Post-hoc framing"]
    end

    subgraph Reporting ["REPORTING"]
        REPORT["Report<br/>━━━━━━━━━━<br/>Boundary disclosed?"]
    end

    %% BOUNDARY FLOWS %%
    HYPO -->|"specified"| DATA
    PLAN -->|"protocol"| DATA
    DATA -->|"analyzed"| FORK
    DATA -->|"framed"| HARK
    FORK -->|"selected"| REPORT
    HARK -.->|"risk"| REPORT

    %% CLASS ASSIGNMENTS %%
    class HYPO,PLAN cli;
    class DATA stateNode;
    class FORK handler;
    class HARK gap;
    class REPORT output;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Blue | Pre-Data | Decisions made before seeing data |
| Teal | Data | Data collection stage |
| Orange | Forking Paths | Post-data analytic choices |
| Amber | HARKing | Hypothesizing After Results Known |
| Dark Teal | Reporting | Final reported results |

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
- `/autoskillit:exp-lens-sensitivity-robustness`
