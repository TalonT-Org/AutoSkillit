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

## When to Use

- Experiment uses randomization or structured assignment
- Need to verify blocking and stratification
- Checking for pseudoreplication
- User invokes `/autoskillit:exp-lens-randomization-blocking` or `/autoskillit:make-experiment-diag randomization`

## Critical Constraints

**NEVER:**
- Modify any source code files
- Assume comparability without tracing its source
- Create files outside `.autoskillit/temp/exp-lens-randomization-blocking/`

**ALWAYS:**
- Trace the exact mechanism that creates comparability between treatment groups
- Identify every nuisance factor and how it is controlled
- Flag pseudoreplication risks (replicating at the wrong unit)
- Verify that replication is adequate for the claimed inferential precision
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Write output to `.autoskillit/temp/exp-lens-randomization-blocking/exp_diag_randomization_blocking_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/.autoskillit/temp/exp-lens-randomization-blocking/exp_diag_randomization_blocking_{...}.md
  %%ORDER_UP%%
  ```

---

## Analysis Workflow

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

Write the diagram to: `.autoskillit/temp/exp-lens-randomization-blocking/exp_diag_randomization_blocking_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

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
