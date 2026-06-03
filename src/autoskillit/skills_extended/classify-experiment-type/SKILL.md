---
name: classify-experiment-type
categories: [research]
backend_requirements: [claude-code]
uses_capabilities: [agent_model, cross_skill_ref]
description: Classify an experiment plan into a type, build the dimension weight matrix, and emit dialing interface tokens for the apply-review-dimensions step.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: classify-experiment-type] Classifying experiment type...'"
          once: true
---

# Classify Experiment Type Skill

Classify an experiment plan into its experiment type, build the dimension weight matrix
with secondary modifier adjustments, detect silent types, and emit structured interface
tokens consumed by `apply-review-dimensions`. This skill is the "dial" step of the
review-design phoropter module — it determines which dimensions and at what weight will
be evaluated.

## When to Use

Invoked as the dial step of the review-design phoropter module, after `plan_experiment`
and before `apply-review-dimensions`. NOT invoked standalone — the recipe step named
`dial` with `phoropter_family: review-design` calls this skill.

## Arguments

`/autoskillit:classify-experiment-type {experiment_plan_path} [{scope_report_path}]`

- **experiment_plan_path** — Absolute path to the experiment plan. Scan tokens after the
  skill name for the first path-like token (starts with `/`, `./`, or `.autoskillit/`).
- **scope_report_path** (optional) — Absolute path to the scope report. Second path-like
  token if present. Forwarded downstream to apply-review-dimensions via recipe context.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.
- Modify files outside `{{AUTOSKILLIT_TEMP}}/classify-experiment-type/`
- Emit verdict (verdict is not an output of this skill)
- Spawn subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Use `Agent(model="sonnet")` for the triage subagent
- Emit `is_silent_type=true|false`
- Emit `dimensions_manifest_path` as an absolute path
- Write output to `{{AUTOSKILLIT_TEMP}}/classify-experiment-type/`
- Issue all subagent calls in a single message to maximize parallel execution

## Workflow

### Step 0: Registry Loading

(a) Locate bundled types directory:

```
python -c "from autoskillit.core import pkg_root; print(pkg_root() / 'recipes' / 'experiment-types')"
```

(b) Glob `*.yaml` in that directory. Parse each file extracting: `name`,
`classification_triggers`, `dimension_weights`, `applicable_lenses`, `red_team_focus`,
`l1_severity`, `dimension_weight_rationale`. If a file is malformed or unreadable, log a
warning and skip it — do not abort registry loading.

(c) Check `.autoskillit/experiment-types/` in the current working directory for user
overrides. A user-defined type with the same `name` replaces the bundled entry entirely
(no field merging). Validate that each user-defined entry contains all required fields
(`name`, `classification_triggers`, `dimension_weights`); if any required field is missing,
log a warning and skip the entry rather than silently accepting a partial spec.

(d) Build registry mapping `type_name → spec`. The set of valid `experiment_type` values
is the set of keys in the registry.

### Step 1: Plan Frontmatter Parsing

Two-level backward-compatible fallback:

- **Level 1 (frontmatter)**: Read YAML between `---` delimiters directly (zero LLM
  tokens). Record `source: frontmatter` for each extracted field. If YAML is malformed,
  treat all fields as missing → fall through to Level 2.
- **Level 2 (LLM extraction)**: For each missing field, launch a targeted extraction
  subagent against the corresponding prose section. All extractions are independent —
  launch all extraction subagents in a single message so they run in parallel. Record
  `source: extracted`.

Fields and prose-target mapping:

| Missing Field | Prose Target | Extraction Prompt |
|---|---|---|
| experiment_type | Full plan | "Classify using the loaded registry types: {', '.join(registry.keys())}" |
| hypothesis_h0/h1 | ## Hypothesis | "Extract the null/alternative hypothesis" |
| estimand | ## Hypothesis + ## Independent Variables | "Extract: treatment, outcome, population, contrast" |
| metrics | ## Dependent Variables table | "Extract each row as structured object" |
| baselines | ## Independent Variables | "Extract comparators: name, version, tuning" |
| statistical_plan | ## Analysis Plan | "Extract: test, alpha, power, correction, sample size" |
| success_criteria | ## Success Criteria | "Extract three criteria" |

### Step 2: Triage Subagent Dispatch

One subagent receives full plan plus parsed fields. Returns:
- `experiment_type`: one of the type names in the loaded registry
- `dimension_weights`: the complete weight matrix (H/M/L/S per dimension)
- `secondary_modifiers`: list of active modifiers with effects

**Schema validation:** If returned `experiment_type` is not in the registry, default to
`exploratory` and log a warning.

**Classification rules (first-match):** Iterate types in registry insertion order
(bundled sorted alphabetically, then user-defined sorted alphabetically). First type whose
`classification_triggers` match the plan is selected. No match → default to `exploratory`.

**Secondary modifiers** (additive, increase — never decrease — dimension weights):
- `+causal`: mechanism claim in non-causal type → `causal_structure` weight +1 tier
- `+high_cost`: resources > 4 GPU-hours → `resource_proportionality` L→M
- `+deployment`: motivation references production/users → `ecological_validity` floor = M
- `+multi_metric`: ≥3 DVs → `statistical_corrections` weight +1 tier

### Step 3: Silent-Type Detection

After classification, evaluate silent-type status against the **BASE registry entry's
`dimension_weights`** (the spec loaded in Step 0), NOT the modifier-adjusted weights
returned by the triage subagent. This matches how the Python `is_silent_type(spec)`
function in `experiment_type_registry.py` operates — it reads
`spec.dimension_weights.values()` from the registry entry directly.

Apply the `is_silent_type` rule: **>=6 of 9 `dimension_weights` == S** →
`is_silent_type=true`.

Reference: `docs/research/silent-type-convention.md`.

**When `is_silent_type=true`:**

1. Write `dimensions_manifest` JSON to
   `{{AUTOSKILLIT_TEMP}}/classify-experiment-type/dimensions_manifest_{slug}_{YYYY-MM-DD_HHMMSS}.json`
   with all-S weights from the base spec plus `secondary_modifiers: []`. This file MUST
   exist at a valid path — `dimensions_manifest_path` always points to a real file, even
   on the silent path.
2. Set `selected_lenses = ''` (empty string)
3. Set `lens_context_paths = ''` (empty string)
4. Emit auto-GO advisory note in structured output
5. Proceed to Step 5 (all output tokens are emitted, including `dimensions_manifest_path`
   pointing to the written file)

**When `is_silent_type=false`:** Proceed to Step 4.

### Step 4: Dimensions Manifest Construction

1. Build `{dimension: weight}` dict from the dimension_weights matrix (applying
   secondary modifier adjustments).
2. Write JSON to
   `{{AUTOSKILLIT_TEMP}}/classify-experiment-type/dimensions_manifest_{slug}_{YYYY-MM-DD_HHMMSS}.json`
   with schema:

   ```json
   {
     "dimensions": {"causal_structure": "H", "variance_protocol": "M", ...},
     "secondary_modifiers": ["+causal", "+multi_metric"]
   }
   ```

3. For each non-SILENT dimension (weight != S), write a lens context file at
   `{{AUTOSKILLIT_TEMP}}/classify-experiment-type/lens_ctx_{dimension}_{slug}_{YYYY-MM-DD_HHMMSS}.md`
   containing: experiment_type, weight, and relevant plan excerpts for that dimension.
4. `selected_lenses` = comma-separated non-SILENT dimension names.
5. `lens_context_paths` = comma-separated absolute paths to per-dimension context files.

### Step 5: Emit Structured Output Tokens

```
experiment_type = {experiment_type}
dimension_weights = {inline summary, e.g., causal_structure:H,variance_protocol:M,...}
is_silent_type = true|false
classification_timestamp = {ISO 8601 UTC}
dimensions_manifest_path = /absolute/path/to/dimensions_manifest_{slug}_{timestamp}.json
selected_lenses = {comma-separated non-SILENT dimension names}
lens_context_paths = {comma-separated absolute paths}
```

Do NOT emit `verdict`. When `is_silent_type=true`, prepend advisory:
`auto-GO: silent type detected — recipe may route around apply-review-dimensions`.

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

## Output

```
{{AUTOSKILLIT_TEMP}}/classify-experiment-type/
├── dimensions_manifest_{slug}_{YYYY-MM-DD_HHMMSS}.json
├── lens_ctx_{dimension1}_{slug}_{YYYY-MM-DD_HHMMSS}.md
├── lens_ctx_{dimension2}_{slug}_{YYYY-MM-DD_HHMMSS}.md
└── ...
```

## Related Skills

- `/autoskillit:review-design` — monolith this skill is extracted from
- `/autoskillit:apply-review-dimensions` — consumes this skill's output
  (`dimensions_manifest_path`, `selected_lenses`, `lens_context_paths`)
- `/autoskillit:plan-experiment` — produces the plan this skill classifies
