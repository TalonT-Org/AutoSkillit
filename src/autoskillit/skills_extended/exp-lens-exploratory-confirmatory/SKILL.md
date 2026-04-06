---
name: exp-lens-exploratory-confirmatory
categories: [exp-lens]
description: Assess whether analytic decisions were pre-specified or post-hoc and whether exploratory/confirmatory norms are aligned. Boundary lens answering "Is this discovery or test, and are norms aligned?"
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Exploratory-Confirmatory Lens - Analyzing boundary integrity...'"
          once: true
---

# Exploratory-Confirmatory Experimental Design Lens

**Philosophical Mode:** Boundary
**Primary Question:** "Is this discovery or test, and are norms aligned?"
**Focus:** Pre-specification, Analytic Flexibility, HARKing Detection, Garden of Forking Paths, Transparent Reporting

## When to Use

- Study mixes exploration and confirmation without clear boundaries
- Post-hoc hypotheses presented as pre-specified
- Many analyses run but only significant ones reported
- User invokes `/autoskillit:exp-lens-exploratory-confirmatory` or `/autoskillit:make-experiment-diag exploratory`

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `.autoskillit/temp/exp-lens-exploratory-confirmatory/`

**ALWAYS:**
- Map the full analytic timeline — what was decided before vs. after seeing data
- Count forking paths honestly — every analysis choice is a potential fork
- Distinguish genuine exploration (hypothesis-generating) from HARKing (hypothesis-after-results)
- Flag absent preregistration as a finding without assuming bad faith
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Write output to `.autoskillit/temp/exp-lens-exploratory-confirmatory/exp_diag_exploratory_confirmatory_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/.autoskillit/temp/exp-lens-exploratory-confirmatory/exp_diag_exploratory_confirmatory_{...}.md
  %%ORDER_UP%%
  ```

---

## Analysis Workflow

### Step 1: Launch Parallel Exploration Subagents

Spawn Explore subagents to investigate:

**Pre-specified Plans**
- Find pre-registration documents, analysis plans, hypothesis files
- Look for: preregister, analysis_plan, hypothesis, registered, protocol, spec

**Analytic Flexibility**
- Find places where multiple analysis paths were possible
- Look for: alternatively, could_also, option, variant, subset, sensitivity, robustness

**Selective Reporting Signals**
- Find evidence of selective reporting or cherry-picking
- Look for: not_significant, excluded, not_shown, supplementary, additional, hidden

**Post-Hoc Rationalization**
- Find language suggesting post-hoc hypothesis generation
- Look for: we_noticed, interestingly, surprisingly, unexpectedly, upon_inspection

**Exploration-Confirmation Separation**
- Find explicit statements about exploratory vs. confirmatory intent
- Look for: exploratory, pilot, hypothesis_generating, confirmatory, pre_specified

### Step 2: Map Analytic Timeline

What was decided before vs. after seeing data? Where is the exploration/confirmation boundary? Count forking paths.

### Step 3: Analyze Boundary Integrity

For every reported result: Was the analysis plan fixed pre-outcome? How many alternatives could have been run? Does reporting distinguish exploratory from confirmatory? Assess survivorship bias.

### Step 4: Create the Diagram (Optional)

**Direction:** LR (time flows left to right). Pre-data decisions → Data collection → Post-data decisions → Reporting

### Step 5: Write Output

Write the output to: `.autoskillit/temp/exp-lens-exploratory-confirmatory/exp_diag_exploratory_confirmatory_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

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
