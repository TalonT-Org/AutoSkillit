---
name: review-design
categories: [research]
description: Validate an experiment plan before execution using triage-first, fail-fast dimensional analysis with an adversarial red-team. Emits verdict (GO/REVISE/STOP), experiment_type, evaluation_dashboard, and revision_guidance.
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
Runs a triage-first, fail-fast multi-level analysis hierarchy with parallel subagents
and an adversarial red-team, then synthesizes a GO/REVISE/STOP verdict.

## When to Use

Use when the research recipe's `review_design` ingredient is `true` (the default). The
recipe calls this skill after `plan_experiment` to gate execution on a quality check.
This skill is bounded by `retries: 2` — on exhaustion the recipe proceeds with the
best available plan.

## Arguments

`/autoskillit:review-design {experiment_plan_path}`

- **experiment_plan_path** — Absolute path to the experiment plan file produced by
  `/autoskillit:plan-experiment`. Scan tokens after the skill name for the first
  path-like token (starts with `/`, `./`, or `.autoskillit/`).

## Critical Constraints

**NEVER:**
- Modify the plan file, any source code, or any file outside `.autoskillit/temp/review-design/`
- Halt the pipeline for a REVISE verdict — emit the verdict and let the recipe route
- Proceed to Level 2, 3, or 4 analysis when any Level 1 finding is critical (halt
  at fail-fast gate)
- Spawn SILENT (S) dimension subagents — they are not run and not mentioned in output
- Exit non-zero — GO, REVISE, and STOP are all normal outcomes (exit 0 in all cases)

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write output to `.autoskillit/temp/review-design/` (relative to the current working directory)
- After writing output files, emit the **absolute paths** as structured output tokens
  immediately before `%%ORDER_UP%%`. Resolve relative save paths to absolute by prepending
  the full CWD:
    verdict = GO|REVISE|STOP
    experiment_type = {type}
    evaluation_dashboard = /absolute/cwd/.autoskillit/temp/review-design/{filename}.md
    revision_guidance = /absolute/cwd/.autoskillit/temp/review-design/{filename}.md   (REVISE only)
    %%ORDER_UP%%
- `revision_guidance` is written and emitted ONLY when verdict = REVISE
- `evaluation_dashboard` is ALWAYS written and emitted
- Red-team agent always sets `requires_decision: true` on all its findings
- Halt at Level 1 fail-fast gate: if any Level 1 finding is critical, emit STOP immediately —
  do NOT proceed to Level 2, 3, or 4

## Workflow

### Step 0: Read Plan & Setup

1. Create `.autoskillit/temp/review-design/` if absent.
2. Extract `experiment_plan_path` from arguments (first path-like token starting with `/`,
   `./`, or `.autoskillit/`).
   **Error handling:** If no path-like token is found in the arguments, emit
   `verdict = STOP` with message "No experiment_plan_path provided" and exit 0 (per
   the NEVER exit-non-zero constraint).
3. Read the plan file.
   **Error handling:** If the file does not exist or is unreadable at the resolved path,
   emit `verdict = STOP` with message "Plan file not found: {path}" and exit 0.
   Parse YAML frontmatter using the **backward-compatible two-level fallback**:
   - **Level 1 (frontmatter)**: Read YAML frontmatter between `---` delimiters directly
     (zero LLM tokens). Return present fields and note which are missing.
     Record `source: frontmatter` for each extracted field.
     **Error handling:** If the YAML between `---` delimiters is malformed, treat all
     fields as missing and fall through to Level 2 for all fields (graceful degradation).
   - **Level 2 (LLM extraction)**: For each missing field, launch a targeted LLM
     extraction subagent against the corresponding prose section. All extractions are
     independent and run in parallel. Record `source: extracted` for each field from
     this path.
   - Fields: `experiment_type`, `hypothesis_h0/h1`, `estimand`, `metrics`, `baselines`,
     `statistical_plan`, `success_criteria`
   - Missing-field to prose-target mapping:

     | Missing Field | Prose Target | Extraction Prompt |
     |---|---|---|
     | experiment_type | Full plan | "Classify: benchmark, configuration_study, causal_inference, robustness_audit, exploratory" |
     | hypothesis_h0/h1 | ## Hypothesis | "Extract the null/alternative hypothesis" |
     | estimand | ## Hypothesis + ## Independent Variables | "Extract: treatment, outcome, population, contrast" |
     | metrics | ## Dependent Variables table | "Extract each row as structured object" |
     | baselines | ## Independent Variables | "Extract comparators: name, version, tuning" |
     | statistical_plan | ## Analysis Plan | "Extract: test, alpha, power, correction, sample size" |
     | success_criteria | ## Success Criteria | "Extract three criteria" |

   This two-level approach ensures backward compatibility with plans that lack frontmatter:
   the provenance (`source: frontmatter` or `source: extracted`) is tracked for each field
   and included in the evaluation dashboard.

### Step 1: Triage Dispatcher

Launch one subagent. Receives full plan text plus parsed fields. Returns:
- `experiment_type`: one of `benchmark | configuration_study | causal_inference |
  robustness_audit | exploratory`
- `dimension_weights`: the complete weight matrix for this plan (H/M/L/S per dimension)
- `secondary_modifiers`: list of active modifiers with their effects on weights

**Schema validation:** After the subagent returns, verify that `experiment_type` is one of
the five enumerated values above. If the returned value is unrecognized, default to
`exploratory` and log a warning — do not silently pass an invalid type into the weight
matrix lookup, as this would corrupt all subsequent spawning decisions.

**Triage classification rules (first-match):**

| Rule | Type | Trigger |
|---|---|---|
| 1 | benchmark | IVs are system/method names, DVs are performance metrics, multiple comparators |
| 2 | configuration_study | IVs are numeric parameters of one system, grid/sweep structure |
| 3 | causal_inference | Causal language ("causes", "effect of"), confounders in threats |
| 4 | robustness_audit | Tests generalization/stability, deliberately varied conditions |
| 5 | exploratory | Default — no prior rule fires, or hypothesis absent |

**Secondary modifiers** (additive, increase dimension weights):
- `+causal`: mechanism claim in non-causal type → causal_structure weight +1 tier
- `+high_cost`: resources > 4 GPU-hours → resource_proportionality L→M
- `+deployment`: motivation references production/users → ecological_validity floor = M
- `+multi_metric`: ≥3 DVs → statistical_corrections weight +1 tier

**Full dimension-to-weight matrix** (W = weight per experiment type):

| Dimension | benchmark | config_study | causal_inf | robust_audit | exploratory |
|---|---|---|---|---|---|
| causal_structure | S | S | H | M | L |
| variance_protocol | H | H | L | M | L |
| statistical_corrections | M | H | H | S | S |
| ecological_validity | M | L | L | H | M |
| measurement_alignment | M | M | M | H | M |
| resource_proportionality | L | L | L | L | L |

Weight tiers: H (High), M (Medium), L (Low), S (SILENT — dimension not spawned, not mentioned).

### Step 2: Level 1 Analysis — Fail-Fast (parallel)

Two subagents run in parallel. Both are always H-weight; severity thresholds are calibrated per experiment_type via the rubric below.

- `estimand_clarity` agent: "Can the claim be written as a formal contrast (A vs B on Y in Z)?"
  Reference the exp-lens-estimand-clarity philosophical mode as guidance (do NOT invoke
  the skill — reference its lens question only in the subagent prompt).
- `hypothesis_falsifiability` agent: "What result would cause the author to conclude H0?"

Each subagent returns findings in the standard JSON structure (see Finding Format below).

**FAIL-FAST GATE**: After both Level 1 subagents complete, check for critical findings.
If ANY Level 1 subagent returns a finding with `"severity": "critical"`:
- Collect these as `stop_triggers`
- Do not proceed to Level 2, 3, or 4 analysis
- Do not start the red-team agent
- Skip directly to Step 7 (Synthesis) with only L1 findings
- The verdict logic will produce STOP; halt and emit tokens

**Subagent parse failure:** If a Level 1 subagent returns unparseable output (malformed
JSON, empty response, token-limit truncation), treat it as if it returned one critical
finding with `message: "L1 subagent did not return parseable findings"`. This ensures
parse failures trigger the fail-fast gate rather than silently passing it.

### Step 3: Level 2 + Red-Team (concurrent)

When the L1 gate passes (no critical L1 findings), launch 2–3 Level 2 subagents AND the
red-team agent concurrently — all at the same time without waiting for each other.

**Level 2 subagents** (parallel, weights from the matrix):
- `baseline_fairness`: "Are all compared systems given symmetric resources and tuning effort?"
- `causal_structure`: weight from matrix (S for benchmark/config_study, H for causal_inference).
  Only spawn when weight ≥ L.
- `unit_interference`: "Can treatments spill over between experimental units?"

**Red-team agent** (concurrent with L2 and L4 — does NOT block L3):
- Receives full plan text and `experiment_type`
- Five universal challenges (challenge every plan regardless of type):
  1. **Goodhart exploitation** — cheapest way to score well without solving the research question
  2. **Data leakage** — test-set info contaminating training/hyperparameter selection
  3. **Asymmetric tuning** — proposed method tuned against eval while baselines use defaults
  4. **Survivorship bias** — cherry-picking best run from multiple seeds
  5. **Evaluation collision** — same infrastructure in both treatment and measurement
- Type-specific focus per experiment type:
  - benchmark → asymmetric effort
  - configuration_study → overfitting to held-out set
  - causal_inference → unblocked backdoor path
  - robustness_audit → unrealistic threat distribution
  - exploratory → HARKing vulnerability
- ALL red-team findings must set `"requires_decision": true` and `"dimension": "red_team"`

### Step 4: Level 3 (parallel)

Run after Level 2 completes (Level 2 findings may inform statistical planning context).
Do not wait for the red-team agent before starting Level 3.

Three subagents run in parallel:
- `error_budget`: "Is power analysis present? Are error rates (Type I / Type II) acknowledged?"
- `statistical_corrections`: "Are multiple comparisons corrections pre-specified for all DVs?"
- `variance_protocol`: "Are seeds fixed? Is run-to-run variance addressed?"
  NOTE: absent seeds IS a valid finding for this dimension at H-weight — do not suppress
  via foothold validation.

### Step 5: Level 4 (parallel, gated by triage)

2–4 subagents. Only spawn subagents for dimensions with weight ≥ L in the matrix.
SILENT (S) dimensions are NOT spawned and NOT mentioned in output.

Level 4 dimensions (spawn when not SILENT):
- `benchmark_representativeness`: "Does this generalize beyond the specific test bed?"
- `ecological_validity`: "Do test conditions match the intended deployment context?"
- `measurement_alignment`: "Do the metrics actually measure what the research question claims?"
- `reproducibility_spec`: "Could an independent party reproduce this experiment?"

Level 3 and Level 4 may run concurrently with the red-team agent (do not block on red-team).

**Three-layer silencing** (prevents orphan warnings):
1. **Static SILENT** from matrix — dimensions not spawned, not mentioned in output.
2. **Foothold validation** — before spawning M/L dimensions, check plan text has relevant
   content. If absent: M→L, L→S. Exception: absent seeds IS a finding for
   `variance_protocol` at H-weight.
3. **Finding-count suppression** — L-weight dimensions with zero findings: omit from output
   entirely. H/M dimensions with zero findings: emit "No issues identified" (deliberate
   clean bill of health).

### Step 6: Wait for Red-Team

After Levels 3 and 4 complete, wait for the red-team agent if still running.
All red-team findings are merged into the finding pool with their
`"requires_decision": true` flag preserved.

### Step 7: Synthesis

One synthesis pass (no subagent — orchestrator synthesizes directly):

1. **Merge all findings** from L1, L2, L3, L4, and red-team into a single list.
2. **Deduplicate** by `(dimension, section, message)` — identical findings from parallel
   agents are collapsed into one entry.
3. **Apply verdict logic**:
   ```python
   # L1 fail-fast path: structural defects that block all further analysis
   stop_triggers = [f for f in critical if f.dimension in {"estimand_clarity", "hypothesis_falsifiability"}]
   # Red-team STOP path: adversarial critical findings after full analysis (L2–L4)
   # These fire only when the L1 gate passed; any critical red_team finding is a STOP.
   stop_triggers += [f for f in critical if f.dimension == "red_team"]

   if stop_triggers:
       verdict = "STOP"
   elif critical_findings or len(warning_findings) >= 3:
       verdict = "REVISE"
   else:
       verdict = "GO"
   ```
4. **Write `evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md`** — always written.
   Must include:
   - Verdict banner and classification summary
   - Dimension scorecard table (dimension → weight → findings count → severity summary)
   - Adversarial findings section (red-team findings, each marked `requires_decision: true`)
   - **Cannot Assess** section with at least 2 items (dimensions where evaluation was
     impossible due to absent plan content; minimum 2 entries, e.g.,
     "Randomization mechanism not described — cannot assess unit interference risk",
     "No resource budgets stated — cannot assess resource_proportionality")
   - Mechanizable check log (binary checks that could be automated in future)
   - Machine-readable YAML summary block at end:
     ```yaml
     # --- review-design machine summary ---
     verdict: GO|REVISE|STOP
     experiment_type: {type}
     critical_count: {n}
     warning_count: {n}
     red_team_count: {n}
     ```
   The YAML summary block enables downstream recipe steps to parse verdict and counts
   without re-reading prose.
5. **Write `revision_guidance_{slug}_{YYYY-MM-DD_HHMMSS}.md`** — written ONLY when
   verdict = REVISE. Must include:
   - Required revisions: critical findings with concrete, actionable fix descriptions
   - Recommended revisions: warning findings
   - Red-team findings with mitigation options
   The `revision_guidance` path is passed back to `plan-experiment` in the recipe loop.

### Step 8: Emit Output Tokens

Emit these lines immediately before `%%ORDER_UP%%`:

```
verdict = GO|REVISE|STOP
experiment_type = {experiment_type}
evaluation_dashboard = /absolute/path/.autoskillit/temp/review-design/evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md
revision_guidance = /absolute/path/.autoskillit/temp/review-design/revision_guidance_{slug}_{YYYY-MM-DD_HHMMSS}.md
%%ORDER_UP%%
```

`revision_guidance` line is emitted ONLY when verdict = REVISE. When verdict is GO or STOP,
omit the `revision_guidance` line entirely.

## Finding Format

All subagents must return findings in this JSON structure:

```json
{
  "section": "## Hypothesis",
  "dimension": "estimand_clarity",
  "level": 1,
  "severity": "critical | warning | info",
  "message": "{clear, actionable description of the issue}",
  "requires_decision": false
}
```

Red-team findings: always `"requires_decision": true`, `"dimension": "red_team"`.

## Output

```
.autoskillit/temp/review-design/
├── evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md   (always written)
└── revision_guidance_{slug}_{YYYY-MM-DD_HHMMSS}.md      (REVISE only)
```

Emit structured output tokens (absolute paths) immediately before `%%ORDER_UP%%`.

## Related Skills

- `/autoskillit:plan-experiment` — produces the plan this skill validates
- `/autoskillit:scope` — first step in the research recipe chain
- `/autoskillit:implement-experiment` — consumes this skill's GO output (via recipe routing)
- exp-lens skills — philosophical modes referenced in dimension prompts (not invoked directly)
