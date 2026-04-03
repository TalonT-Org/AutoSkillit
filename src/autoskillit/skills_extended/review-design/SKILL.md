---
name: review-design
categories: [research]
description: Validate an experiment plan before execution. Emits verdict (GO/REVISE/STOP), experiment_type, evaluation_dashboard, and revision_guidance.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: review-design] Reviewing experiment design...'"
          once: true
---

# Review Design Skill

Validate the quality and feasibility of an experiment plan before compute is spent.
Returns a structured verdict to drive the research recipe's design review loop.

## Arguments

`/autoskillit:review-design {experiment_plan_path}`

- **experiment_plan_path** — Absolute path to the experiment plan file

## When to Use

Use when the research recipe's `review_design` ingredient is `true` (the default). The
recipe calls this skill after `plan_experiment` to gate execution on a quality check.
This skill is bounded by `retries: 2` — on exhaustion the recipe proceeds with the
best available plan.

## Critical Constraints

**NEVER:**
- Write output outside `.autoskillit/temp/review-design/`
- Halt the pipeline for a REVISE verdict — emit the verdict and let the recipe route

**ALWAYS:**
- Emit `verdict = GO`, `verdict = REVISE`, or `verdict = STOP` on the final output line
- Write `evaluation_dashboard` and `revision_guidance` as absolute paths when present

## Output

Emit on the final line of output:

```
verdict = {GO|REVISE|STOP}
experiment_type = {string}
evaluation_dashboard = {absolute_path}
revision_guidance = {absolute_path}
%%ORDER_UP%%
```
