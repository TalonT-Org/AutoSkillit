---
name: apply-review-dimensions
categories: [research]
uses_capabilities: [agent_model, cross_skill_ref]
description: Evaluate experiment design across weighted dimensions using multi-level subagent analysis with adversarial red-team, producing a findings manifest and evaluation dashboard.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: apply-review-dimensions] Evaluating review dimensions...'"
          once: true
---

# Apply Review Dimensions Skill

Evaluate an experiment plan across its classified dimension weight matrix using a
multi-level subagent hierarchy (L1 fail-fast → L2 + red-team → L3 → L4) with
three-layer silencing. Produces a findings manifest and evaluation dashboard. Does
NOT emit a verdict — verdict computation is the responsibility of a downstream
synthesis step.

## When to Use

Invoked as the apply step of the review-design phoropter module, after
`classify-experiment-type` and before the downstream verdict synthesis step. NOT
invoked standalone — the recipe step named `apply` with
`phoropter_family: review-design` calls this skill.

## Arguments

`/autoskillit:apply-review-dimensions {dimensions_manifest_path} {scope_report_path} {experiment_plan_path} {experiment_type} {classification_timestamp}`

- **dimensions_manifest_path** — Absolute path to the JSON file emitted by the
  classify step's `select_review_dimensions` run_python callable. Contains
  `{dimension: weight}` pairs plus a `secondary_modifiers` list.
- **scope_report_path** — Absolute path to the scope report. Forwarded via recipe
  context variable `${{ context.scope_report }}` threaded through as a direct
  `with: args:` positional argument. This is the mechanism P3-A3/P3-A4 must use
  when wiring the apply step in recipe YAML. NOT captured from
  classify-experiment-type output.
- **experiment_plan_path** — Absolute path to the experiment plan file.
- **experiment_type** — Snake-case experiment type name from classification.
- **classification_timestamp** — ISO 8601 UTC timestamp from classification.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.
- Emit verdict (verdict is computed by `aggregate_review_verdict` in the synthesize step)
- Spawn background subagents (`run_in_background: true` is prohibited)
- Write outside `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/`
- Proceed past L1 when any STRUCTURAL critical finding is present
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Use `Agent(model="sonnet")` for all subagents
- Write `findings_manifest` and `evaluation_dashboard` before emitting tokens
- Emit both output tokens as absolute paths
- Write output to `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/`
- Issue all subagent calls in a single message to maximize parallel execution

## Workflow

### Step 0: Setup

1. Create `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/` if absent.
2. Load dimensions_manifest JSON from `dimensions_manifest_path`.
3. **Empty / all-silent detection:** If all dimension weights are `S` (SILENT) or
   the dimension list is empty:
   - Write an empty `findings_manifest` JSON (empty array) to
     `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/findings_manifest_{slug}_{YYYY-MM-DD_HHMMSS}.json`
   - Write a "Scope Advisory" `evaluation_dashboard` to
     `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md`
   - Emit output tokens and return without spawning any subagents.

### Step 1: L1 Fail-Fast Gate (Level 1)

Two parallel subagents, each weight is H (highest priority — L1 is the gate that
halts on STRUCTURAL criticals):

- `estimand_clarity` — "Can the claim be written as a formal contrast (A vs B on
  Y in Z)?"
- `hypothesis_falsifiability` — "What result would cause the author to conclude
  H0?"

Severity calibration from the experiment_type registry's `l1_severity` dict.

Each subagent returns findings in JSON structure.

**ADDRESSABLE vs STRUCTURAL classification:**
- **ADDRESSABLE** — Concrete methodological flaw with a mechanical fix. The
  research question remains answerable after revision.
- **STRUCTURAL** — Research question is not answerable with this experimental
  design regardless of revision.

**Classification scope limitation:** Only `hypothesis_falsifiability` findings
are eligible for ADDRESSABLE. `estimand_clarity` findings default to STRUCTURAL.

**Gate logic:**
- Any STRUCTURAL critical → halt L2-L4. Write findings with what's available and
  proceed directly to Step 6.
- All criticals ADDRESSABLE → tag as `"priority": "REQUIRED"` and continue to
  Step 2.

### Step 2: L2 + Red-Team (Level 2, concurrent)

Launch all L2 subagents AND the red-team agent in the same parallel message.

**L2 subagents** (weights from matrix):
- `baseline_fairness` — "Are all compared systems given symmetric resources and
  tuning effort?"
- `causal_structure` — Only spawn when weight >= L. "Can cause/effect be
  isolated?"
- `unit_interference` — "Can treatments spill over between experimental units?"
- `scope_alignment` — Only spawn when (a) `scope_report_path` provided AND (b)
  weight >= L. "Does the plan address the proposed investigation directions?"

**`scope_alignment` evaluation procedure:**

1. **Direction extraction:** Read "Proposed Investigation Directions" from the
   scope report. Enumerate as D1, D2, D3...
   - Empty section guard: If absent or no enumerable directions, emit an Info
     finding and return.
2. **Coverage mapping:** For each direction, check if the plan addresses it via
   hypothesis, arm, or measurement.
3. **Coverage ratio:** `covered_count / total_directions`
4. **Narrowing detection:** If `total_directions >= 3` and `covered_count == 1`,
   flag as unjustified narrowing unless explicit exclusion rationale exists.
5. **Justification check:** Directions with explicit rationale for deferral are
   "justified-excluded."

Findings rules for `scope_alignment`:
- Critical (weight=H): coverage < 50% AND no justification
- Warning: coverage < 50% with partial justification, OR narrows to 1 from >= 3
- Info: coverage >= 50% but some uncovered lack justification

**Red-team agent** (concurrent with L2):
- Five universal challenges: Goodhart exploitation, data leakage, asymmetric
  tuning, survivorship bias, evaluation collision
- Type-specific focus: from registry's `red_team_focus.specific` field
- ALL findings: `"requires_decision": true`, `"dimension": "red_team"`

### Step 3: L3 (Level 3, after L2, no wait for red-team)

Three parallel subagents, each receiving the full plan + experiment_type + L1+L2
findings summary:

- `error_budget` — "Is power analysis present? Are error rates acknowledged?"
- `statistical_corrections` — "Are multiple comparisons corrections
  pre-specified?"
- `variance_protocol` — "Are seeds fixed? Is run-to-run variance addressed?"

NOTE: absent seeds IS a valid finding at H-weight — do not suppress via foothold
validation. This is the explicit exception to the foothold validation rule.

### Step 4: L4 (Level 4, spawn only weight >= L)

Up to six subagents:

- `benchmark_representativeness` — "Does this generalize beyond the specific
  test bed?"
- `ecological_validity` — "Do test conditions match deployment context?"
- `measurement_alignment` — "Do metrics actually measure what the question
  claims?"
- `reproducibility_spec` — "Could an independent party reproduce this?"
- `data_acquisition` — Full acquisition checklist: hypothesis coverage, external
  source readiness, gitignored path handling, dependency ordering, directive
  compliance, template syntax validation. STOP-eligible if: hypothesis has no
  data source, directive-specified data has no acquisition step, unresolved
  template placeholders, wet_lab sources present.
- `agent_implementability` — Step atomicity, file path resolvability, performance
  feasibility, verification criteria completeness, dependency ordering, absence
  of human-only actions, artifact continuity. REVISE-eligible only.

### Three-Layer Silencing Rules

1. **Static SILENT from matrix:** S-weight dimensions are not spawned and not
   mentioned in the output.
2. **Foothold validation:** Before spawning M/L dimensions, check the plan has
   relevant content. If absent: M → L, L → S. Exception: `variance_protocol`
   absent seeds is a valid H-weight finding (do not suppress).
3. **Finding-count suppression:** L-weight zero findings → omit entirely. H/M
   zero findings → emit "No issues identified."
4. **Scope-report absent:** `scope_alignment` is treated SILENT regardless of
   weight when `scope_report_path` is absent.

### Step 5: Wait for Red-Team

After L3 and L4 complete, wait for the red-team agent if it is still running.
Merge all red-team findings into the finding pool with
`requires_decision: true` preserved.

### Step 6: Write Findings Manifest

Write
`{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/findings_manifest_{slug}_{YYYY-MM-DD_HHMMSS}.json`.

**Explicit JSON schema (machine contract):**

```json
{
  "findings": [
    {
      "dimension": "estimand_clarity",
      "level": 1,
      "severity": "critical",
      "finding": "description of gap",
      "addressable": true,
      "requires_decision": false,
      "priority": "REQUIRED",
      "fixability": "ADDRESSABLE",
      "message": "human-readable finding message"
    }
  ],
  "red_team_findings": [
    {
      "dimension": "red_team",
      "level": 2,
      "severity": "warning",
      "finding": "adversarial concern",
      "addressable": false,
      "requires_decision": true,
      "priority": "ADVISORY",
      "fixability": null,
      "message": "human-readable red-team finding"
    }
  ]
}
```

The required fields for every finding are: `dimension`, `level`, `severity`,
`finding`, `addressable`, `requires_decision`, `priority`, `fixability`,
`message`. The top-level object has a `findings` array and a `red_team_findings`
sub-array.

### Step 7: Write Evaluation Dashboard

Write
`{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md`
always (full-analysis path or STRUCTURAL halt path).

**Full-analysis path contents:**
- Dimension scorecard table: dimension → weight → findings count → severity
  summary
- Adversarial findings section (red-team findings, each `requires_decision: true`)
- **Cannot Assess** section (minimum 2 entries)
- Mechanizable check log
- Machine-readable YAML summary block

**STRUCTURAL halt path contents** (when only L1 data is available):
- Dimension scorecard showing only L1 dimensions (`estimand_clarity`,
  `hypothesis_falsifiability`)
- Adversarial findings section: "N/A — halted before red-team launch"
- Cannot Assess section: minimum 2 entries (dimensions that would have been
  evaluated had L1 passed)
- Mechanizable check log: fixed items only
- Machine-readable YAML summary block with `active_dimensions: 2`,
  `red_team_count: 0`

**Machine-readable YAML summary block:**

```yaml
# --- apply-review-dimensions machine summary ---
experiment_type: {type}
critical_count: {n}
warning_count: {n}
blocking_count: {n}
required_count: {n}
advisory_count: {n}
red_team_count: {n}
active_dimensions: {n}
```

NOTE: NO `verdict` field in this YAML block — verdict is computed by
`aggregate_review_verdict` in the downstream synthesis step.

### Step 8: Emit Structured Output Tokens

```
findings_manifest_path = {{AUTOSKILLIT_TEMP}}/apply-review-dimensions/findings_manifest_{slug}_{YYYY-MM-DD_HHMMSS}.json
evaluation_dashboard_path = {{AUTOSKILLIT_TEMP}}/apply-review-dimensions/evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md
```

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

No `verdict` token. No `revision_guidance` token.

## Output

Output tokens (relative to the current working directory):

- `findings_manifest_path` — `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/findings_manifest_{slug}_{YYYY-MM-DD_HHMMSS}.json`
- `evaluation_dashboard_path` — `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md`

```
{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/
├── findings_manifest_{slug}_{YYYY-MM-DD_HHMMSS}.json   (always)
└── evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md  (always)
```

## Related Skills

- `/autoskillit:classify-experiment-type` — produces `dimensions_manifest_path`
  input
- Downstream synthesis step — consumes `findings_manifest_path` to compute
  verdict (verdict computation lives in the synthesis step, not in this skill)
- `/autoskillit:plan-experiment` — produces `experiment_plan_path`
- `/autoskillit:scope` — produces `scope_report_path`

## Context Limit Behavior

When context is exhausted mid-execution, the `findings_manifest_path` and
`evaluation_dashboard_path` in `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/`
may be partially written but missing the deeper L3/L4 review findings. The
recipe's `on_context_limit` route triggers `create_worktree`, preserving
whatever findings were captured so the downstream synthesis step can still
compute a verdict from the available evidence.
