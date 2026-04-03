---
name: exp-lens-error-budget
categories: [exp-lens]
description: Analyze statistical error budget showing Type I/II errors, power, minimum detectable effect, multiplicity corrections, and sequential monitoring. Statistical lens answering "Are error risks sized and controlled?"
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Error Budget Lens - Analyzing statistical error risks...'"
          once: true
---

# Error Budget Experimental Design Lens

**Philosophical Mode:** Statistical
**Primary Question:** "Are error risks sized and controlled?"
**Focus:** Type I/II Errors, Power, Minimum Detectable Effect, Multiplicity, Sequential Monitoring

## When to Use

- Need to verify statistical power before running an experiment
- Multiple comparisons are performed without a stated correction strategy
- Sequential testing or interim analysis is in use without defined stopping rules
- User invokes `/autoskillit:exp-lens-error-budget` or `/autoskillit:make-experiment-diag error`

## Critical Constraints

**NEVER:**
- Modify any source code files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Accept default alpha=0.05 without checking whether it is appropriate for the decision context
- Create files outside `.autoskillit/temp/exp-lens-error-budget/`

**ALWAYS:**
- Enumerate every statistical test and account for its error contribution
- Distinguish per-test error rates from family-wise error rates
- Flag any sequential peeking without a formal stopping rule as a critical defect
- Evaluate whether the minimum detectable effect is practically meaningful, not just statistically chosen
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- Write output to `.autoskillit/temp/exp-lens-error-budget/exp_diag_error_budget_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/.autoskillit/temp/exp-lens-error-budget/exp_diag_error_budget_{...}.md
  %%ORDER_UP%%
  ```

---

## Analysis Workflow

### Step 1: Launch Parallel Exploration Subagents

Spawn Explore subagents to investigate:

**Sample Size & Power**
- Find power calculations or sample size justifications
- Look for: power, sample_size, n_samples, effect_size, minimum_detectable, mde

**Multiple Comparisons**
- Find all statistical tests performed and correction strategies
- Look for: bonferroni, fdr, holm, bh, correction, multiple, comparisons, tests

**Sequential Analysis**
- Find interim analyses, stopping rules, or sequential monitoring
- Look for: interim, early_stopping, sequential, alpha_spending, peek, monitor

**Decision Thresholds**
- Find significance thresholds and decision rules
- Look for: alpha, p_value, threshold, significance, reject, null, hypothesis

**Effect Size Context**
- Find practical significance alongside statistical significance
- Look for: effect_size, cohen, practical, meaningful, magnitude, difference

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

Write the analysis to: `.autoskillit/temp/exp-lens-error-budget/exp_diag_error_budget_{YYYY-MM-DD_HHMMSS}.md`

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
