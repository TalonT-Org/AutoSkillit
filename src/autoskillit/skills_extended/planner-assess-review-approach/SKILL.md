---
name: planner-assess-review-approach
categories: [planner]
description: >
  Assess each work package for review-approach benefit before implementation.
  Writes review_approach_assessment.json; does NOT invoke review-approach.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-assess-review-approach] Assessing review-approach benefit...'"
          once: true
---

# planner-assess-review-approach

Assessment-only pass that evaluates each work package for review-approach benefit.
Reads `refined_wps.json` and `analysis.json`, spawns subagents to evaluate WPs, and
writes `review_approach_assessment.json` to the planner directory. Does NOT invoke
`review-approach` — assessment only.

## Arguments

- **$1** — Absolute path to `refined_wps.json` (PlanDocument with `task`, `work_packages[]`)
- **$2** — Absolute path to the planner output directory (for `analysis.json` and output)

## Critical Constraints

**NEVER:**
- Invoke `review-approach` — this skill performs assessment only
- Write output outside `$2/`
- Modify input files
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Read the `review-approach` SKILL.md at `src/autoskillit/skills_extended/review-approach/SKILL.md` before assessing
- Read `$1` to get `task` and `work_packages[]`
- Read `$2/analysis.json` for codebase technology context
- Write `$2/review_approach_assessment.json`
- Emit: `review_approach_assessment_path = <absolute path to review_approach_assessment.json>`

## Workflow

### Step 1: Ground heuristics

Read `src/autoskillit/skills_extended/review-approach/SKILL.md`. Understand what
`review-approach` does and when it provides value. Do not rely solely on the hardcoded
signals below — use the SKILL.md as the authoritative source for benefit criteria.

### Step 2: Read inputs

Read `$1` to extract the `task` field and `work_packages[]` list. Read `$2/analysis.json`
for codebase technology context: available libraries, architectural patterns in use, and
established conventions. This context informs whether a WP is "following established patterns"
(no-benefit) versus "introducing something new" (benefit signal).

### Step 3: Evaluate each WP

Spawn 1–2 subagents (model: "sonnet") to evaluate WPs in parallel batches. For each WP,
evaluate against these signals:

**Benefit signals (recommend: true):**
- Involves integrating an unfamiliar external library or API
- Proposes a design decision with multiple viable architectural approaches
- References emerging patterns, standards, or technologies not yet in the codebase
- Contains open questions about *how* to approach the problem
- Requires understanding trade-offs between competing solutions

**No-benefit signals (recommend: false):**
- Well-scoped bug fix with a clear root cause
- Internal refactoring following established codebase patterns
- Adds a feature using patterns already present in the codebase
- Documentation update or configuration change
- Already contains a fully specified implementation approach in the WP body

Per WP, produce: `review_approach_recommended` (bool) and `review_approach_reasoning`
(one sentence).

### Step 4: Write output

Write `$2/review_approach_assessment.json`:

```json
{
  "schema_version": 1,
  "assessments": [
    {
      "wp_id": "P1-A1-WP1",
      "review_approach_recommended": true,
      "review_approach_reasoning": "WP requires evaluating trade-offs between two competing persistence strategies."
    }
  ]
}
```

Example path: `{{AUTOSKILLIT_TEMP}}/planner/run-20260502-120000/review_approach_assessment.json`

### Step 5: Emit output token

```
review_approach_assessment_path = $2/review_approach_assessment.json
```

## Context Limit Behavior

This skill writes `$2/review_approach_assessment.json` before emitting structured output
tokens. If context is exhausted mid-execution:

1. Before emitting any structured output tokens, verify that `review_approach_assessment.json`
   exists in `$2/`.
2. If the file exists, emit the structured token and exit normally.
3. If context exhaustion interrupts before the file is written, the caller's
   `on_context_limit` routing handles escalation — do not attempt partial output.
