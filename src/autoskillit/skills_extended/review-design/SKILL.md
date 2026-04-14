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
- Modify the plan file, any source code, or any file outside `{{AUTOSKILLIT_TEMP}}/review-design/`
- Halt the pipeline for a REVISE verdict — emit the verdict and let the recipe route
- Proceed to Level 2, 3, or 4 analysis when any Level 1 finding is classified as
  STRUCTURAL (halt at fail-fast gate). ADDRESSABLE L1 criticals continue L2-L4.
- Spawn SILENT (S) dimension subagents — they are not run and not mentioned in output
- Exit non-zero — GO, REVISE, and STOP are all normal outcomes (exit 0 in all cases)
- Include code snippets, shell commands, or specific tool invocations in findings or revision guidance — findings describe gaps and risks, not implementation instructions
- Prescribe HOW to fix an issue — findings must describe WHAT is lacking or at risk; the fix is the plan author's responsibility

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write output to `{{AUTOSKILLIT_TEMP}}/review-design/` (relative to the current working directory)
- After writing output files, emit the **absolute paths** as structured output tokens
  immediately before `%%ORDER_UP%%`. Resolve relative save paths to absolute by prepending
  the full CWD:
    verdict = GO|REVISE|STOP
    experiment_type = {type}
    evaluation_dashboard = /absolute/cwd/{{AUTOSKILLIT_TEMP}}/review-design/{filename}.md
    revision_guidance = /absolute/cwd/{{AUTOSKILLIT_TEMP}}/review-design/{filename}.md   (REVISE only)
    %%ORDER_UP%%
- `revision_guidance` is written and emitted ONLY when verdict = REVISE
- `evaluation_dashboard` is ALWAYS written and emitted
- Red-team agent always sets `requires_decision: true` on all its findings
- Halt at Level 1 fail-fast gate: if any Level 1 finding is classified as STRUCTURAL,
  emit STOP immediately — do NOT proceed to Level 2, 3, or 4. If all L1 criticals are
  ADDRESSABLE, tag them as REQUIRED priority fixes and continue L2-L4 analysis.

## Context Limit Behavior

When context is exhausted mid-execution, output files may be partially written or
absent. The recipe routes to `on_context_limit`, abandoning the partial review.

**Before emitting structured output tokens:**
1. If `evaluation_dashboard` was not fully written, emit `verdict = STOP` as a safe fallback
2. Omit `revision_guidance` if not written; the orchestrator handles the context-limit route

## Workflow

### Step 0: Read Plan & Setup

1. Create `{{AUTOSKILLIT_TEMP}}/review-design/` if absent.
2. Extract `experiment_plan_path` from arguments (first path-like token starting with `/`,
   `./`, or `.autoskillit/`).
   **Error handling:** If no path-like token is found in the arguments, emit
   `verdict = STOP` with message "No experiment_plan_path provided" and exit 0 (per
   the NEVER exit-non-zero constraint).
3. Read the plan file.
   **Error handling:** If the file does not exist or is unreadable at the resolved path,
   emit `verdict = STOP` with message "Plan file not found: {path}" and exit 0.
4. **Load the experiment type registry:**
   a. Locate bundled types dir: run
      `python -c "from autoskillit.core import pkg_root; print(pkg_root() / 'recipes' / 'experiment-types')"`
      to get the absolute bundled directory path.
   b. Use Glob `*.yaml` in that directory, then Read each file. Parse YAML frontmatter to
      extract `name`, `classification_triggers`, `dimension_weights`, `applicable_lenses`,
      `red_team_focus`, and `l1_severity` fields from each.
   c. Check `.autoskillit/experiment-types/` in the current working directory. If it exists,
      read all `*.yaml` files there. A user-defined type with the same `name` as a bundled
      type replaces the bundled entry entirely — do not merge fields.
   d. The resulting registry is a mapping of type name → spec. The set of valid
      `experiment_type` values for this run is the set of keys in the registry.
5. Parse YAML frontmatter using the **backward-compatible two-level fallback**:
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
     | experiment_type | Full plan | "Classify using the loaded registry types: {', '.join(registry.keys())}" |
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
- `experiment_type`: one of the type names in the loaded registry (from Step 0)
- `dimension_weights`: the complete weight matrix for this plan (H/M/L/S per dimension)
- `secondary_modifiers`: list of active modifiers with their effects on weights

**Schema validation:** After the subagent returns, verify that `experiment_type` is a key
in the loaded registry (from Step 0). If the returned value is not in the registry, default
to `exploratory` and log a warning — do not silently pass an invalid type into the weight
matrix lookup, as this would corrupt all subsequent spawning decisions.

**Triage classification rules (first-match):**

Use the `classification_triggers` list from each type in the loaded registry to classify
the experiment. Apply first-match: iterate types in registry insertion order (bundled types
sorted alphabetically, then user-defined types sorted alphabetically). The first type whose
trigger description matches the plan is selected. If no trigger matches, default to
`exploratory`.

**Secondary modifiers** (additive, increase dimension weights):
- `+causal`: mechanism claim in non-causal type → causal_structure weight +1 tier
- `+high_cost`: resources > 4 GPU-hours → resource_proportionality L→M
- `+deployment`: motivation references production/users → ecological_validity floor = M
- `+multi_metric`: ≥3 DVs → statistical_corrections weight +1 tier

**Dimension weights:**

Use the `dimension_weights` dict from the matched type's registry entry (loaded in Step 0).
Each key is a dimension name; each value is one of weight=H (High), weight=M (Medium), weight=L (Low),
or weight=S (SILENT — dimension not spawned, not mentioned in output). Pass the full
`dimension_weights` dict to the triage subagent so it can return the complete weight
matrix for this plan.

### Subagent Evaluation Scope (applies to ALL dimension subagents)

**Include this instruction block in every dimension subagent prompt.**

Every finding must describe WHAT is lacking or at risk in the experimental design.
Never prescribe HOW to fix it — the fix is the plan author's responsibility.

- GOOD: "The plan does not address how implementation correctness will be verified before measurement"
- BAD: "The plan must contain apply_phase1_changes.sh with inline Python that greps for function ordering"

- GOOD: "The step-timing instrumentation could introduce inter-iteration contamination if reset ordering is incorrect"
- BAD: "step_timing::reset() uses Ordering::Relaxed — change to Ordering::Release"

Findings must never include:
- Code snippets or shell commands as required plan content
- Specific tool invocations, package versions, or file paths as mandated fixes
- Implementation-level instructions (these belong in implementation phases, not design review)

**Design scope boundary:**

Evaluate the experimental DESIGN: hypotheses, metrics, statistical methodology,
controlled variables, threats to validity, data acquisition strategy, reproducibility specification.

Do NOT evaluate:
- Correctness of implementation code snippets included as illustrative examples in the plan
- Shell script syntax, path accuracy, or command correctness in plan examples
- Package version pinning, compile-time constants, or infrastructure code correctness

If a code snippet in the plan reveals a design-level concern (e.g., the metric definition
contradicts the hypothesis), flag the design concern, not the code bug.

### Step 2: Level 1 Analysis — Fail-Fast (parallel)

Two subagents run in parallel. Both are always H-weight; severity thresholds are calibrated per experiment_type via the rubric below.

Each L1 subagent receives as explicit inputs:
- Full plan text
- `experiment_type` (from Step 1 triage output)
- Severity calibration rubric (below)

**Severity calibration rubric for L1 dimensions:**

Use the `l1_severity` dict from the matched experiment type's registry entry (loaded in
Step 0). Keys are `estimand_clarity` and `hypothesis_falsifiability`; values are severity
levels (`critical`, `warning`, `info`). Calibration anchors: `causal_inference` → critical;
`benchmark`, `configuration_study`, `robustness_audit` → warning; `exploratory` → info.

- `estimand_clarity` agent: "Can the claim be written as a formal contrast (A vs B on Y in Z)?"
  Reference the exp-lens-estimand-clarity philosophical mode as guidance (do NOT invoke
  the skill — reference its lens question only in the subagent prompt).
  Use the `l1_severity.estimand_clarity` value from the registry to assign severity.
- `hypothesis_falsifiability` agent: "What result would cause the author to conclude H0?"
  Use the `l1_severity.hypothesis_falsifiability` value from the registry to assign severity.

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

**ADDRESSABLE vs STRUCTURAL classification**: After collecting critical L1 findings,
classify each one before applying the gate:

- **ADDRESSABLE**: Concrete methodological flaw with a mechanical fix — the research
  question remains answerable after revision (e.g., "decompose composite hypothesis
  into independent pairs", "add explicit estimand contrast statement")
- **STRUCTURAL**: The research question is not answerable with this experimental design
  regardless of revision (e.g., "no observable outcome exists for this hypothesis",
  "estimand requires counterfactual data that cannot be collected")

**Classification scope limitation**: Initially, only `hypothesis_falsifiability` findings
are eligible for ADDRESSABLE classification — hypothesis restructuring is the dimension
most likely to produce mechanically fixable defects. `estimand_clarity` findings default
to STRUCTURAL (absent estimands typically indicate deeper design flaws).

**Gate behavior after classification:**
- If ANY critical finding is STRUCTURAL → halt L2-L4 analysis (emit STOP)
- If ALL critical findings are ADDRESSABLE → tag each as `"priority": "REQUIRED"`,
  continue L2-L4 analysis. The verdict becomes REVISE (not STOP) with the ADDRESSABLE
  findings at the top of the evaluation dashboard.
- If mixed (some ADDRESSABLE, some STRUCTURAL) → halt (STRUCTURAL takes precedence)

### Step 3: Level 2 + Red-Team (concurrent)

When the L1 gate passes (no STRUCTURAL critical L1 findings — gate also passes when all L1 criticals are ADDRESSABLE), launch 2–3 Level 2 subagents AND the
red-team agent concurrently — all at the same time without waiting for each other.

**Level 2 subagents** (parallel, weights from the matrix):
- `baseline_fairness`: "Are all compared systems given symmetric resources and tuning effort?"
- `causal_structure`: weight from matrix (S for benchmark/config_study, H for causal_inference).
  Only spawn when weight ≥ L.
- `unit_interference`: "Can treatments spill over between experimental units?"

**Red-team agent** (concurrent with L2 and L4 — does NOT block L3):

Receives: full plan text and `experiment_type` (from Step 1 triage output)

- Five universal challenges (challenge every plan regardless of type):
  1. **Goodhart exploitation** — cheapest way to score well without solving the research question
  2. **Data leakage** — test-set info contaminating training/hyperparameter selection
  3. **Asymmetric tuning** — proposed method tuned against eval while baselines use defaults
  4. **Survivorship bias** — cherry-picking best run from multiple seeds
  5. **Evaluation collision** — same infrastructure in both treatment and measurement
- Type-specific focus: use `red_team_focus.specific` from the matched type's registry
  entry (loaded in Step 0).
- ALL red-team findings must set `"requires_decision": true` and `"dimension": "red_team"`

**Red-team severity calibration rubric:**

| Dimension | causal_inference | benchmark | configuration_study | robustness_audit | exploratory |
|-----------|-----------------|-----------|---------------------|------------------|-------------|
| red_team  | critical        | warning   | warning             | warning          | info        |

The red-team agent assigns severity based on the intrinsic quality of each finding.
After the red-team agent returns, **cap each finding's severity** to the maximum
allowed by the experiment type using this rubric — identical to how L1 severity
calibration works. For `causal_inference`: critical red-team findings remain critical
(STOP-eligible). For `benchmark`/`configuration_study`/`robustness_audit`: critical
findings are downgraded to `warning` (REVISE-eligible but never STOP). For
`exploratory`: all red-team findings are capped at `info` (informational only).

This cap is applied in Step 7 before the verdict logic evaluates `stop_triggers`.

### Step 4: Level 3 (parallel)

Run after Level 2 completes. Do not wait for the red-team agent before starting Level 3.

Each L3 subagent receives:
- Full plan text
- `experiment_type` (from Step 1 triage output) — calibrates expected statistical rigor:
  `exploratory` plans do not require pre-registered correction procedures; `causal_inference`
  plans demand formal power analysis and correction pre-specification.
- L1 and L2 findings summary as context (findings may inform statistical planning relevance)

Three subagents run in parallel:
- `error_budget`: "Is power analysis present? Are error rates (Type I / Type II) acknowledged?"
- `statistical_corrections`: "Are multiple comparisons corrections pre-specified for all DVs?"
- `variance_protocol`: "Are seeds fixed? Is run-to-run variance addressed?"
  NOTE: absent seeds IS a valid finding for this dimension at H-weight — do not suppress
  via foothold validation.

### Step 5: Level 4 (parallel, gated by triage)

2–4 subagents. Only spawn subagents for dimensions with weight ≥ L in the matrix.
SILENT (S) dimensions are NOT spawned and NOT mentioned in output.

Each L4 subagent receives:
- Full plan text
- `experiment_type` (from Step 1 triage output) — calibrates rigor expectations per dimension:
  `benchmark` plans have lower ecological validity expectations than `causal_inference` plans
  by design; `reproducibility_spec` rigor scales with `causal_inference` > `benchmark` >
  `exploratory`.
- `dimension_weights` (from Step 1) — provides context on why this dimension was spawned
  (e.g., H-weight dimensions warrant stricter thresholds than L-weight dimensions)

Level 4 dimensions (spawn when not SILENT):
- `benchmark_representativeness`: "Does this generalize beyond the specific test bed?"
- `ecological_validity`: "Do test conditions match the intended deployment context?"
- `measurement_alignment`: "Do the metrics actually measure what the research question claims?"
- `reproducibility_spec`: "Could an independent party reproduce this experiment?"
- `data_acquisition`: "Does the plan include a complete data acquisition strategy?"
- `agent_implementability`: "Is this plan executable by a code-generating agent without human intervention?"

#### `data_acquisition` — Data Acquisition Completeness

Validates that the experiment plan includes a complete data acquisition strategy:

1. **Hypothesis coverage**: Every hypothesis in `success_criteria` has at least one
   `data_manifest` entry specifying its data source.
2. **External source readiness**: Every entry with `source_type: external` has an
   explicit acquisition command and a verification criterion.
3. **Gitignored path handling**: Every entry with `source_type: gitignored` has an
   acquisition/generation step — gitignored paths are empty in fresh worktrees.
4. **Dependency ordering**: If entry A's `depends_on` references entry B's acquisition,
   B must be listed before A (or the dependency chain must be acyclic).
5. **Directive compliance**: If the research task directive specifies particular data,
   the `data_manifest` must include acquisition steps for that data.

**Findings format:**
- STOP if: a hypothesis has no data source at all, or directive-specified data has no acquisition step
- REVISE if: an external source lacks verification criteria, or gitignored path handling is unclear

#### `agent_implementability` — Agent Execution Feasibility

Validates that the experiment plan can be implemented by a code-generating agent
without human intervention:

1. **Step atomicity**: Each implementation step has a single unambiguous action.
   Multiple interleaved actions in a single step create ambiguity about ordering
   and completion criteria.
2. **File path resolvability**: All referenced files and modules are named with
   locatable paths. Vague references like "the utils module" or "the config file"
   are insufficient — the agent needs exact paths or unambiguous naming conventions.
3. **Performance feasibility**: Pseudocode and algorithms are viable at the
   specified data scale. A nested loop that is O(n^2) at n=50K is a design-level
   concern, not an implementation detail.
4. **Verification criteria completeness**: Each implementation phase has a
   concrete, runnable acceptance test or verification criterion. Phases without
   verification criteria cannot be confirmed complete by an agent.
5. **Dependency ordering**: No step requires an artifact (file, dataset, model
   checkpoint) that has not yet been produced by a prior step. Circular or
   forward dependencies make deterministic execution impossible.
6. **Absence of human-only actions**: No steps require subjective judgment,
   visual inspection, or domain expertise that a code-generating agent cannot
   execute deterministically. Examples: "review the output and decide if it looks
   reasonable", "manually inspect the plot for anomalies".
7. **Artifact continuity**: No planned artifact from a prior plan version was
   silently removed without replacement or explicit justification. Removals
   without explanation are data provenance regressions.

**Findings format:**
- REVISE if: any of checks 1–7 identifies a gap in the plan
- This dimension does not produce STOP-eligible findings (L4 contract)

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
3. **Consolidate related findings**: If multiple findings address the same underlying
   methodological concern (e.g., four separate findings about multiple comparisons
   correction from different dimensions), group them as sub-findings under a single
   parent finding with the highest severity and priority of the group. The parent
   finding's message should describe the shared concern; sub-findings retain their
   dimension-specific detail as bullet points beneath the parent. This prevents
   the same issue from inflating finding counts and obscuring distinct problems.
4. **Apply red-team severity cap, then verdict logic**:
   ```python
   # RT_MAX_SEVERITY is built from the registry loaded in Step 0 (dict-of-dicts from YAML parsing):
   RT_MAX_SEVERITY = {name: spec["red_team_focus"]["severity_cap"] for name, spec in registry.items()}
   SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
   rt_cap = RT_MAX_SEVERITY[experiment_type]

   for f in findings:
       if f.dimension == "red_team" and SEVERITY_RANK[f.severity] > SEVERITY_RANK[rt_cap]:
           f.severity = rt_cap  # downgrade before verdict

   # Reclassify after cap
   critical_findings = [f for f in findings if f.severity == "critical"]
   warning_findings = [f for f in findings if f.severity == "warning"]

   active_dimensions = count_of_spawned_dimensions  # tracked from Steps 2-6

   # Proportional warning threshold: each active dimension gets a budget of 5
   # warnings before the plan is flagged for revision.
   WARNING_BUDGET_PER_DIM = 5
   warning_threshold = active_dimensions * WARNING_BUDGET_PER_DIM

   # L1 fail-fast path: only STRUCTURAL defects trigger STOP
   l1_criticals = [f for f in critical_findings if f.dimension in {"estimand_clarity", "hypothesis_falsifiability"}]
   # Tag ADDRESSABLE L1 criticals as REQUIRED (scope: hypothesis_falsifiability only)
   for f in l1_criticals:
       if f.fixability == "ADDRESSABLE":
           f.priority = "REQUIRED"
   # Scope guard: estimand_clarity always STRUCTURAL; None fixability defaults to STRUCTURAL
   structural_stop_triggers = [
       f for f in l1_criticals
       if f.fixability == "STRUCTURAL" or f.fixability is None or f.dimension == "estimand_clarity"
   ]

   # Red-team STOP path: adversarial critical findings after full analysis (L2-L4)
   # These fire only when the L1 gate passed AND the severity cap still allows critical.
   stop_triggers = structural_stop_triggers + [f for f in critical_findings if f.dimension == "red_team"]

   if stop_triggers:
       verdict = "STOP"
   elif critical_findings or len(warning_findings) >= warning_threshold:
       verdict = "REVISE"
   else:
       verdict = "GO"
   ```
5. **Write `evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md`** — always written.
   Must include:
   - Verdict banner and classification summary
   - Dimension scorecard table (dimension → weight → findings count → severity summary)
   - Adversarial findings section (red-team findings, each marked `requires_decision: true`)
   - **Cannot Assess** section with at least 2 items (dimensions where evaluation was
     impossible due to absent plan content; minimum 2 entries, e.g.,
     "Randomization mechanism not described — cannot assess unit interference risk",
     "No resource budgets stated — cannot assess resource_proportionality")
   - Mechanizable check log — fixed checklist items (always evaluated) plus
     ad-hoc additions from subagents:
     - **Fixed:** "All implementation phases have runnable verification criteria"
     - **Fixed:** "All file paths in the implementation plan resolve to valid locations"
     - Ad-hoc entries contributed by dimension subagents during this review cycle
   - Machine-readable YAML summary block at end:
     ```yaml
     # --- review-design machine summary ---
     verdict: GO|REVISE|STOP
     experiment_type: {type}
     critical_count: {n}
     warning_count: {n}
     blocking_count: {n}
     required_count: {n}
     advisory_count: {n}
     red_team_count: {n}
     active_dimensions: {n}
     warning_threshold: {n}
     ```
   The YAML summary block enables downstream recipe steps to parse verdict and counts
   without re-reading prose.
6. **Write `revision_guidance_{slug}_{YYYY-MM-DD_HHMMSS}.md`** — written ONLY when
   verdict = REVISE. Must include:
   - Required revisions: critical findings with gap and risk descriptions (not implementation instructions)
   - Recommended revisions: warning findings with gap and risk descriptions
   - Red-team findings with risk descriptions and decision points
   Revision guidance describes WHAT needs to change in the experimental design,
   never HOW to implement the change. The `revision_guidance` path is passed back
   to `plan-experiment` in the recipe loop.

### Step 8: Emit Output Tokens

Emit these lines immediately before `%%ORDER_UP%%`:

```
verdict = GO|REVISE|STOP
experiment_type = {experiment_type}
evaluation_dashboard = /absolute/path/{{AUTOSKILLIT_TEMP}}/review-design/evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md
revision_guidance = /absolute/path/{{AUTOSKILLIT_TEMP}}/review-design/revision_guidance_{slug}_{YYYY-MM-DD_HHMMSS}.md
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
  "priority": "BLOCKING | REQUIRED | ADVISORY",
  "fixability": "ADDRESSABLE | STRUCTURAL | null",
  "message": "{describes what is lacking or at risk — never prescribes how to fix}",
  "requires_decision": false
}
```

**Fixability classification** (L1 findings only — all other levels use JSON `null`):

See **ADDRESSABLE vs STRUCTURAL classification** in Step 2 for authoritative definitions
and scope limitations. `null` (JSON null literal, not the string "null") is used for all
non-L1 findings where fixability is not applicable.

**Priority tiers** (supplementing, not replacing, `severity`):

- **BLOCKING**: Must be addressed before implementation proceeds. Structural invalidity
  that would make results uninterpretable (e.g., "metric |Delta T_B| is mathematically
  invalid for Approach B — entire experimental arm produces meaningless data")
- **REQUIRED**: Must be addressed in revision. Methodology gaps that weaken but do not
  invalidate results (e.g., "composite hypothesis is unfalsifiable — decompose into
  independent pairs")
- **ADVISORY**: Should be addressed but omission will not invalidate results (e.g.,
  "gitignore coverage for generated artifacts not specified")

**Priority assignment rules:**
- STRUCTURAL L1 criticals → BLOCKING
- ADDRESSABLE L1 criticals → REQUIRED
- Non-L1 criticals → BLOCKING (default) or REQUIRED (if finding describes a gap
  rather than an invalidity)
- Warnings → REQUIRED (default) or ADVISORY (if finding is informational in nature)
- Info → ADVISORY

Red-team findings: always `"requires_decision": true`, `"dimension": "red_team"`.

## Output

```
{{AUTOSKILLIT_TEMP}}/review-design/
├── evaluation_dashboard_{slug}_{YYYY-MM-DD_HHMMSS}.md   (always written)
└── revision_guidance_{slug}_{YYYY-MM-DD_HHMMSS}.md      (REVISE only)
```

Emit structured output tokens (absolute paths) immediately before `%%ORDER_UP%%`.

## Related Skills

- `/autoskillit:plan-experiment` — produces the plan this skill validates
- `/autoskillit:scope` — first step in the research recipe chain
- `/autoskillit:implement-experiment` — consumes this skill's GO output (via recipe routing)
- exp-lens skills — philosophical modes referenced in dimension prompts (not invoked directly)
