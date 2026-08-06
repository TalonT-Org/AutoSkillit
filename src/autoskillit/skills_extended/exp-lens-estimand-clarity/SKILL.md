---
name: exp-lens-estimand-clarity
categories:
- exp-lens
uses_capabilities: []
activate_deps:
- mermaid
description: Create Estimand Clarity experimental design analysis decomposing the implicit estimand from code vs. explicit
  claims from prose. Evidential lens answering "What exactly is the claim?"
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: echo 'Estimand Clarity Lens - Analyzing claim precision...'
      once: true
semantic_version: 1
semantic_requirements:
  sibling_skills:
  - name: exp-lens-causal-assumptions
  - name: exp-lens-measurement-validity
  - name: make-experiment-diag
  - name: mermaid
---

# Estimand Clarity Experimental Design Lens

**Philosophical Mode:** Evidential
**Primary Question:** "What exactly is the claim?"
**Focus:** Effect Definition, Target Population, Outcome Specification, Comparator, Aggregation Level, Complication Handling

## Arguments

`/autoskillit:exp-lens-estimand-clarity [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Experiment has unclear or shifting hypotheses
- Multiple stakeholders interpret results differently
- Claims mix causal and predictive language
- User invokes `/autoskillit:exp-lens-estimand-clarity` or `/autoskillit:make-experiment-diag estimand`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code or experiment files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-estimand-clarity/`
- Detach child delegations instead of joining them (joining every child is required)
- Run exploration leaves in the background
- Start independent child delegations sequentially

**ALWAYS:**
- Decompose every stated claim into formal contrast notation (Treatment A vs Treatment B on Outcome Y in Population Z)
- Flag every mismatch between prose claims and code implementation
- Identify the aggregation level (unit, group, time) explicitly
- Document how complications (missing data, failures, exclusions) are handled
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Start all independent child delegations before awaiting any result to maximize concurrency
- Use the registered exploration roles for all repository reads
- Dispatch exactly 6 exploration vectors through the deterministic router
- Route semantic code, symbol, and data-control-flow handoffs to `semantic-code-navigator` and bounded configuration, data, fixture, manifest, generated-artifact, reproduction, test, and pre-existing revision-scoped artifact handoffs to `repository-impact-profiler` through the parent-owned plan
- Wait for every exploration result before extracting the implicit estimand, comparing claims to code, or creating the diagram
- Retain parent authority over estimand formalization and claim-alignment judgments, Mermaid generation, and output writing
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-estimand-clarity/exp_diag_estimand_clarity_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-estimand-clarity/exp_diag_estimand_clarity_{...}.md
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

<!-- autoskillit:exploration-vector id="stated-claims-hypotheses" -->
1. **Stated Claims & Hypotheses** — Find hypothesis statements, research questions, and repository claims through `hypothesis`, `claim`, `goal`, `objective`, `question`, `we show`, `we demonstrate`, `improves`, and `outperforms` evidence.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="treatment-definition" -->
2. **Treatment Definition** — Find what intervention or manipulation is applied through `treatment`, `intervention`, `method`, `approach`, `condition`, `configuration`, and `ablation` definitions and call paths. Route bounded configuration and ablation artifacts through the parent to the profiler.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="outcome-definition" -->
3. **Outcome Definition** — Find what is measured as the result through `outcome`, `metric`, `measure`, `endpoint`, `target`, `response`, and `dependent` definitions and call paths. Route bounded metric configuration and result artifacts through the parent to the profiler.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="population-scope" -->
4. **Population & Scope** — Find what units, datasets, and contexts the claim covers through `dataset`, `population`, `sample`, `domain`, `task`, `benchmark`, `scenario`, and `setting` evidence.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="complication-handling" -->
5. **Complication Handling** — Find how missing data, failures, timeouts, and exclusions are handled through `missing`, `exclude`, `timeout`, `fail`, `drop`, `impute`, `censor`, and `incomplete` control flow. Route bounded fixtures and result artifacts through the parent to the profiler.
<!-- /autoskillit:exploration-vector -->

### Step 2: Extract the Implicit Estimand

Answer each question from the code (not the docs):
1. What is the treatment?
2. What is the comparator?
3. What is the outcome?
4. What is the population?
5. What is the time horizon?
6. How are complications handled?

### Step 3: Compare Claims to Implementation

Compare the explicit claims (from docs/papers) to the implicit estimand (from code). Flag mismatches between what the prose asserts and what the implementation actually measures.

**CRITICAL — Analyze Claim Precision:**
For every stated claim:
- Can you write it as a formal contrast (Treatment A vs Treatment B on Outcome Y in Population Z)? If not, what is ambiguous?
- Does the code measure what the prose claims?

### Step 4: Create the Optional Claim-Flow Diagram

If a diagram adds value, create a simplified flowchart. This is OPTIONAL for this hybrid lens — the tables are the primary output.

**Direction:** `TB` (claim flows from intervention through measurement to conclusion)

**Small diagram: 4-6 nodes showing Treatment → Mechanism → Outcome → Claim**

**Node Styling:**
- `cli` class: treatment/intervention nodes
- `handler` class: mechanism/pipeline nodes
- `output` class: measured outcome nodes
- `phase` class: stated claim nodes
- `gap` class: ambiguity or mismatch between claim and measurement

### Step 5: Write Output

Write the analysis to: `{{AUTOSKILLIT_TEMP}}/exp-lens-estimand-clarity/exp_diag_estimand_clarity_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

---

## Output Template

```markdown
# Estimand Clarity Analysis: {Experiment Name}

**Lens:** Estimand Clarity (Evidential)
**Question:** What exactly is the claim?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}

## Estimand Decomposition

| Component | Stated | Implemented | Match? |
|-----------|--------|-------------|--------|
| Treatment | {from prose} | {from code} | Yes / No / Partial |
| Comparator | {from prose} | {from code} | Yes / No / Partial |
| Outcome | {from prose} | {from code} | Yes / No / Partial |
| Population | {from prose} | {from code} | Yes / No / Partial |
| Time Horizon | {from prose} | {from code} | Yes / No / Partial |
| Complication Handling | {from prose} | {from code} | Yes / No / Partial |

## Claim Precision Assessment

| Claim | Formal Contrast | Ambiguities |
|-------|----------------|-------------|
| "{stated claim}" | Treatment A vs B on Y in Z | {list ambiguities} |

## Claim-Flow Diagram (Optional)

```mermaid
%%{init: {'flowchart': {'nodeSpacing': 50, 'rankSpacing': 60, 'curve': 'basis'}}}%%
flowchart TB
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

    TREATMENT["Treatment<br/>━━━━━━━━━━<br/>{intervention name}"]
    MECHANISM["Mechanism<br/>━━━━━━━━━━<br/>{pipeline step}"]
    OUTCOME["Measured Outcome<br/>━━━━━━━━━━<br/>{metric}"]
    CLAIM["Stated Claim<br/>━━━━━━━━━━<br/>{claim text}"]
    MISMATCH["Mismatch<br/>━━━━━━━━━━<br/>{ambiguity description}"]

    TREATMENT --> MECHANISM
    MECHANISM --> OUTCOME
    OUTCOME --> CLAIM
    OUTCOME -.->|"diverges"| MISMATCH

    class TREATMENT cli;
    class MECHANISM handler;
    class OUTCOME output;
    class CLAIM phase;
    class MISMATCH gap;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Blue | Treatment | Intervention applied |
| Orange | Mechanism | Pipeline processing |
| Dark Teal | Outcome | Measured result |
| Purple | Claim | Stated conclusion |
| Yellow | Mismatch | Ambiguity or claim-code divergence |

## Ambiguity Register

| # | Ambiguity | Location | Severity | Resolution Needed |
|---|-----------|----------|----------|-------------------|
| 1 | {description} | {file/section} | High / Medium / Low | {what to clarify} |

## Recommendations

1. {Specific action to resolve most critical ambiguity}
2. {Rewrite suggestion for vague claim}
3. {Code change to align implementation with stated estimand}
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
- `/autoskillit:exp-lens-causal-assumptions` - For causal structure of the stated claim
- `/autoskillit:exp-lens-measurement-validity` - For whether the outcome metric is valid
