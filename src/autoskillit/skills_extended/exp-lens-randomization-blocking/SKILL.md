---
name: exp-lens-randomization-blocking
categories: [exp-lens]
activate_deps: [mermaid]
description: Create Randomization & Blocking experimental design diagram showing assignment mechanisms, blocking factors, and comparability sources. Design-Structural lens answering "Where does comparability come from?"
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Randomization & Blocking Lens - Analyzing assignment and comparability...'"
          once: true
---

# Randomization & Blocking Experimental Design Lens

**Philosophical Mode:** Design-Structural
**Primary Question:** "Where does comparability come from?"
**Focus:** Assignment Mechanisms, Blocking Factors, Stratification, Balanced Designs, Replication

## Arguments

`/autoskillit:exp-lens-randomization-blocking [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Experiment uses randomization or structured assignment
- Need to verify blocking and stratification
- Checking for pseudoreplication
- User invokes `/autoskillit:exp-lens-randomization-blocking` or `/autoskillit:make-experiment-diag randomization`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Assume comparability without tracing its source
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-randomization-blocking/`
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Trace the exact mechanism that creates comparability between treatment groups
- Identify every nuisance factor and how it is controlled
- Flag pseudoreplication risks (replicating at the wrong unit)
- Verify that replication is adequate for the claimed inferential precision
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-randomization-blocking/exp_diag_randomization_blocking_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-randomization-blocking/exp_diag_randomization_blocking_{...}.md
  ```

---

## Analysis Workflow

### Step 0: Parse optional arguments

If positional arg 1 (context_path) is provided and the file exists, read it to obtain
IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria. If positional
arg 2 (experiment_plan_path) is provided and exists, read the experiment plan for full
methodology. Use this structured context as the foundation for Steps 1-5; skip the CWD
exploration for these fields if the context file supplies them. For any field absent from the context file, perform CWD exploration for that specific field only.

### Step 1: Launch Parallel Exploration Subagents

Spawn Explore subagents to investigate:

**Assignment Mechanism**
- Find how experimental units are assigned to conditions
- Look for: random, assign, allocate, split, stratify, block, hash, bucket

**Blocking & Stratification**
- Find blocking factors and stratification variables
- Look for: block, strata, stratify, covariate, match, pair, group_by

**Replication Structure**
- Find how many independent replicates exist per condition
- Look for: replicate, repeat, trial, run, seed, fold, n_replications

**Order & Timing Effects**
- Find potential for carryover or order effects
- Look for: order, sequence, carryover, period, washout, crossover, time

**Exclusion & Attrition**
- Find how units are excluded or drop out during the experiment
- Look for: exclude, drop, attrition, missing, censor, incomplete, filter

### Step 2: Map the Allocation Flow

Trace: Population → assignment → analysis. Identify randomization unit, blocking factors, replication adequacy, and potential confounds.

### Step 3: CRITICAL — Analyze Comparability Source

Distinguish: True randomization / Blocked randomization / Matched pairs / Deterministic assignment

For each: Is the comparability mechanism strong enough for the claimed inference?

### Step 4: Create the Diagram

**Direction:** TB. Subgraphs: POPULATION/POOL, BLOCKING, RANDOMIZATION, TREATMENT ARMS, ANALYSIS

### Step 5: Write Output

Write the diagram to: `{{AUTOSKILLIT_TEMP}}/exp-lens-randomization-blocking/exp_diag_randomization_blocking_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

---

## Output Template

```markdown
# Randomization & Blocking Design: {Experiment Name}

**Lens:** Randomization & Blocking (Design-Structural)
**Question:** Where does comparability come from?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}

## Allocation Flow

| Unit | Assignment Mechanism | Blocking Factors | Strata Count | Replication Adequacy |
|------|---------------------|-------------------|--------------|---------------------|
| {unit} | {mechanism} | {factors} | {count} | {Adequate/Marginal/Inadequate} |

## Comparability Analysis

| Comparability Source | Strength | Pseudoreplication Risk | Confound |
|---------------------|----------|----------------------|----------|
| {source} | {Strong/Moderate/Weak} | {High/Medium/Low/None} | {confound or None} |

## Allocation Diagram

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

    subgraph PopulationPool ["POPULATION/POOL"]
        POP["Population<br/>━━━━━━━━━━<br/>Eligible units"]
        SCREEN["Screening<br/>━━━━━━━━━━<br/>Inclusion criteria"]
    end

    subgraph Blocking ["BLOCKING"]
        BLOCK["Blocking Factor<br/>━━━━━━━━━━<br/>Strata definition"]
        STRATA["Strata<br/>━━━━━━━━━━<br/>N strata"]
    end

    subgraph Randomization ["RANDOMIZATION"]
        RAND["Assignment<br/>━━━━━━━━━━<br/>Mechanism"]
    end

    subgraph TreatmentArms ["TREATMENT ARMS"]
        TX["Treatment<br/>━━━━━━━━━━<br/>N_tx units"]
        CTRL["Control<br/>━━━━━━━━━━<br/>N_ctrl units"]
    end

    subgraph Analysis ["ANALYSIS"]
        ANAL["Comparison<br/>━━━━━━━━━━<br/>Estimator"]
    end

    %% ALLOCATION FLOWS %%
    POP -->|"screened"| SCREEN
    SCREEN -->|"eligible"| BLOCK
    BLOCK -->|"stratified"| STRATA
    STRATA -->|"within-stratum"| RAND
    RAND -->|"assigned"| TX
    RAND -->|"assigned"| CTRL
    TX -->|"measured"| ANAL
    CTRL -->|"measured"| ANAL

    %% CLASS ASSIGNMENTS %%
    class POP,SCREEN cli;
    class BLOCK,STRATA phase;
    class RAND handler;
    class TX,CTRL stateNode;
    class ANAL output;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Blue | Population | Eligible units and screening |
| Purple | Blocking | Blocking factors and strata |
| Orange | Randomization | Assignment mechanism |
| Teal | Treatment Arms | Treatment and control groups |
| Dark Teal | Analysis | Comparison and estimation |

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
- `/autoskillit:exp-lens-causal-assumptions`
- `/autoskillit:exp-lens-unit-interference`
