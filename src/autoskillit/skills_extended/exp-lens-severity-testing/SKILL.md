---
name: exp-lens-severity-testing
categories: [exp-lens]
activate_deps: [mermaid]
description: Analyze severity of experimental tests — adversarial cases, negative controls, falsification tests, easy-pass detection, and confirmatory theater. Falsificationist lens answering "Would this design have caught the error?"
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Severity Testing Lens - Analyzing adversarial robustness of experimental conclusions...'"
          once: true
---

# Severity Testing Experimental Design Lens

**Philosophical Mode:** Falsificationist
**Primary Question:** "Would this design have caught the error?"
**Focus:** Adversarial Cases, Negative Controls, Falsification Tests, Easy-Pass Detection, Confirmatory Theater

## Arguments

`/autoskillit:exp-lens-severity-testing [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Evaluating whether positive results are meaningful or trivially achievable
- Checking for adversarial robustness of experimental conclusions
- User invokes `/autoskillit:exp-lens-severity-testing` or `/autoskillit:make-experiment-diag severity`

## Critical Constraints

**NEVER:**
- Modify any source code files
- Accept a "pass" result without asking what a false result would have looked like under this design
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-severity-testing/`

**ALWAYS:**
- For every positive claim, identify what error the test was capable of detecting
- Inventory negative controls and sanity checks explicitly — their absence is a finding
- Rate severity before reporting conclusions, not after
- Flag confirmatory theater: experiments designed to confirm rather than risk refutation
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-severity-testing/exp_diag_severity_testing_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-severity-testing/exp_diag_severity_testing_{...}.md
  %%ORDER_UP%%
  ```

---

## Analysis Workflow

### Step 0: Parse optional arguments

If positional arg 1 (context_path) is provided and the file exists, read it to obtain
IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria. If positional
arg 2 (experiment_plan_path) is provided and exists, read the experiment plan for full
methodology. Use this structured context as the foundation for Steps 1-5; skip the CWD
exploration for these fields if the context file supplies them.

### Step 1: Launch Parallel Exploration Subagents

Spawn Explore subagents to investigate:

**Positive Results Claimed**
- Find all conclusions and positive claims in the experiment
- Look for: demonstrates, improves, outperforms, achieves, shows, confirms, validates

**Negative Controls & Sanity Checks**
- Find negative controls, baselines, and sanity check tests
- Look for: negative_control, sanity, ablation, degenerate, trivial, null, random

**Adversarial Conditions**
- Find adversarial or stress-test conditions applied
- Look for: adversarial, attack, stress, perturbation, corruption, noise, edge_case

**Alternative Explanations Tested**
- Find whether alternative explanations were examined
- Look for: alternative, confound, artifact, spurious, coincidence, luck

**Prediction Specificity**
- Find how specific the predictions were before seeing data
- Look for: prediction, hypothesis, preregistered, expected, prior

### Step 2: Assess Severity for Each Claim

For each claim:
1. What error was the test capable of detecting?
2. What would a false positive result have looked like under this design?
3. Were negative controls or sanity checks included?
4. Were adversarial conditions tested?
5. Is the test informative (would a bad result look different from a good result)?

### Step 3: Rate Severity and Identify Gaps

Severity ratings: HIGH / MEDIUM / LOW
Flag **confirmatory theater** when design is structured to confirm rather than risk refutation.

### Step 4: Create Optional Severity-Flow Diagram

Show Claims → HIGH/MEDIUM/LOW severity tests → Severity verdicts.

### Step 5: Write Output

Write the analysis to: `{{AUTOSKILLIT_TEMP}}/exp-lens-severity-testing/exp_diag_severity_testing_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

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
- `/autoskillit:exp-lens-error-budget`
- `/autoskillit:exp-lens-validity-threats`
