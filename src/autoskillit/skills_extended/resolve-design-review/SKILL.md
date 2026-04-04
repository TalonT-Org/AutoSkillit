---
name: resolve-design-review
categories: [research]
description: >
  Triage STOP verdict findings from review-design, classifying each as
  ADDRESSABLE/STRUCTURAL/DISCUSS using parallel subagents. If any are ADDRESSABLE
  or DISCUSS, generate revision_guidance and emit resolution=revised. If all are
  STRUCTURAL, emit resolution=failed for terminal stop.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: resolve-design-review] Triaging design review STOP findings...'"
          once: true
---

# Resolve Design Review Skill

Triage STOP verdict findings from `review-design`, perform feasibility
analysis to classify each finding as ADDRESSABLE/STRUCTURAL/DISCUSS using
parallel feasibility-validation subagents, generate revision guidance for
addressable findings, and emit routing token to feed back into the revision
loop or halt.

## Arguments

`/autoskillit:resolve-design-review <evaluation_dashboard_path> <experiment_plan_path>`

## When to Use

Called by the research recipe via run_skill when review_design emits verdict=STOP.
MCP-only — not user-invocable directly.

## Critical Constraints

**NEVER:**
- Create files outside `.autoskillit/temp/resolve-design-review/`
- Modify the evaluation dashboard, experiment plan, or any source file
- Apply fixes — this skill triages fixability only

**ALWAYS:**
- Exit 0 in all cases — resolution=revised and resolution=failed are both normal outcomes
- Emit revision_guidance ONLY when resolution=revised
- Use model: "sonnet" for all subagents

## Workflow

### Step 0: Validate Arguments and Parse Dashboard

1. Create `.autoskillit/temp/resolve-design-review/` if absent
2. Parse two positional path arguments: `evaluation_dashboard_path`, `experiment_plan_path`
   - If missing: print `"Error: missing required argument(s) — expected <evaluation_dashboard_path> <experiment_plan_path>"`, then emit `resolution=failed`, exit 0
   - If file not found: print `"Error: file not found — {missing_path}"`, then emit `resolution=failed`, exit 0
3. Parse stop-trigger findings from the evaluation dashboard:
   - Locate machine-readable YAML block (`# --- review-design machine summary ---`)
   - Extract critical findings from L1 dimensions (estimand_clarity, hypothesis_falsifiability)
   - Extract red_team critical findings
   - If no findings parseable: treat all as DISCUSS → emit resolution=revised with generic guidance; add a `> **Warning:** dashboard could not be parsed — falling back to generic guidance` annotation at the top of the revision_guidance file so the parse failure is visible in pipeline logs

### Step 1: Feasibility Validation (Parallel Subagents — BEFORE any guidance is written)

This is the analysis phase. It runs entirely before any guidance is generated.

Group findings; launch one parallel Task subagent per finding (model: "sonnet").
Each subagent receives: finding metadata + full plan text.
Each subagent classifies the finding as:

- **ADDRESSABLE** — concrete methodological flaw with a mechanical fix
  (fix is well-defined, e.g., "set --iterations >= 3 at all n values")
- **STRUCTURAL** — fundamental unfixability: research question not answerable
  with this design regardless of revision
- **DISCUSS** — valid design question requiring human judgment; fix is not mechanical

Each subagent returns:
```json
{
  "verdict": "ADDRESSABLE|STRUCTURAL|DISCUSS",
  "evidence": "specific references from plan text",
  "fix_sketch": "brief concrete fix description (ADDRESSABLE only)"
}
```

Fallback: failed/timed-out subagent → classify finding as DISCUSS (safe, routes to revision).

Write analysis report to `.autoskillit/temp/resolve-design-review/analysis_{slug}_{ts}.md`
BEFORE any guidance is generated. Report must include summary banner:
```
Triage complete (BEFORE any guidance written)
ADDRESSABLE: N | STRUCTURAL: N | DISCUSS: N
```

### Step 2: Apply Resolution Logic

```
resolution = "revised" when ANY finding is ADDRESSABLE or DISCUSS
resolution = "failed"  only when ALL findings are STRUCTURAL
```

### Step 3: Write Revision Guidance (only when resolution = revised)

Write `revision_guidance_{slug}_{ts}.md` to `.autoskillit/temp/resolve-design-review/`

Sections:
1. **Required Fixes** — ADDRESSABLE findings with fix_sketch from subagent
2. **Design Questions for Human Review** — DISCUSS findings flagged for human awareness
3. **Structural Findings (for context)** — STRUCTURAL findings listed (if any)

### Step 4: Report and Emit Structured Output Tokens

Print summary:
```
resolve-design-review complete
Stop triggers triaged: {total}
  ADDRESSABLE: {n}
  STRUCTURAL: {n}
  DISCUSS: {n}
Resolution: {revised|failed}
```

IMPORTANT: Emit the structured output tokens as **literal plain text with no
markdown formatting on the token names**. Do not wrap token names in `**bold**`,
`*italic*`, or any other markdown. The adjudicator performs a regex match on the
exact token name — decorators cause match failure.

When resolution = revised, emit immediately before `%%ORDER_UP%%`:

```
resolution = revised
revision_guidance = /absolute/path/.autoskillit/temp/resolve-design-review/revision_guidance_{slug}_{ts}.md
%%ORDER_UP%%
```

When resolution = failed, emit immediately before `%%ORDER_UP%%`:

```
resolution = failed
%%ORDER_UP%%
```

`revision_guidance` is ONLY emitted when resolution = revised.

## Output

All output files are written to `.autoskillit/temp/resolve-design-review/` relative to
the current working directory.

```
.autoskillit/temp/resolve-design-review/
├── analysis_{slug}_{ts}.md          (always written — before any guidance)
└── revision_guidance_{slug}_{ts}.md  (revised path only)
```
