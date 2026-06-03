---
name: select-vis-lenses
categories: [research, vis-lens]
uses_capabilities: [agent_model]
phoropter_family: vis-lens
description: >
  Dial step of the vis-lens phoropter: parses experiment plan fields, applies
  three-tier lens selection (Tier A mandatory, Tier B experiment-type, Tier C
  methodology tradition), writes per-lens context files, and emits five
  structured tokens for downstream vis-lens invocation.
---

# Select Vis-Lenses Skill

Reads the finalized experiment plan, selects 2–4 vis-lens skills via three-tier
logic, writes per-lens context files, and emits structured tokens for downstream
consumption. Does NOT invoke the vis-lens skills — that is handled by the
`run_vis_lenses` recipe step.

## When to Use

- As the `dial` step of the vis-lens phoropter in `research-design.yaml` and
  `research.yaml`, after `review_design` GO and before `run_vis_lenses`

## Arguments

```
/autoskillit:select-vis-lenses {source_dir} {experiment_plan_path} {scope_report_path}
```

- `{source_dir}` — Absolute path to the source repo (the CWD before worktree creation)
- `{experiment_plan_path}` — Absolute path to the finalized experiment plan markdown
- `{scope_report_path}` — Absolute path to the scope report (may be empty string if absent)

## Critical Constraints

**NEVER:**
- Select fewer than 2 or more than 4 lenses
- Skip vis-lens-always-on (it is always Tier A)
- Write outputs outside `{{AUTOSKILLIT_TEMP}}/select-vis-lenses/`
- Fabricate lens recommendations or validation conclusions not supported by the data — report what the experiment plan and scope show, not what you assume they should show
- Run subagents in the background (`run_in_background: true` is prohibited)
- Invoke vis-lens skills — that belongs to the `run_vis_lenses` recipe step
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Spawn all subagents via `Agent(model="sonnet")`
- Write a vis-lens context file for each selected lens before emitting tokens
- Issue all Task calls in a single message to maximize parallelism

## Workflow

### Step 0 — Parse Arguments

Extract `source_dir`, `experiment_plan_path`, and `scope_report_path` from arguments.
Read the experiment plan at `experiment_plan_path`.

Extract the following fields (use sensible defaults if absent):
- `experiment_type` — string (e.g., "benchmark", "causal_inference", "exploratory")
- `training_curves` — boolean (default: false)
- `num_DVs` — integer count of dependent variables (default: 1)
- `comparative` — boolean (true if multiple conditions compared head-to-head; default: false)
- `DV_types` — list of DV type strings (e.g., ["accuracy", "temporal", "latency"]; default: [])
- `num_conditions` — integer count of experimental conditions (default: 1)
- `target_domain` — string (e.g., "nlp", "cv", "rl", "general")

### Step 1 — Three-Tier Lens Selection

Build `selected_lenses` (list of 2–4 vis-lens skill slugs):

**Tier A (always selected, mandatory):**
- `vis-lens-always-on`

**Tier B (select 1–2 based on experiment_type and override rules):**

Override rules (checked first, in priority order):
1. If `training_curves == true` → include `vis-lens-temporal`
2. If `num_DVs >= 6 AND comparative == true` → include `vis-lens-multi-compare`
3. If `DV_types` contains `"temporal"` → include `vis-lens-temporal` (if not already)
4. If `num_conditions >= 8` → include `vis-lens-multi-compare` (if not already)

Experiment-type table (use when no override fires or to fill second Tier-B slot):
| experiment_type | Primary lens | Secondary lens (optional) |
|---|---|---|
| benchmark | vis-lens-chart-select | vis-lens-uncertainty |
| causal_inference | vis-lens-chart-select | vis-lens-figure-table |
| configuration_study | vis-lens-chart-select | vis-lens-uncertainty |
| evidence_synthesis | vis-lens-chart-select | vis-lens-figure-table |
| exploratory | vis-lens-chart-select | vis-lens-uncertainty |
| factorial_design | vis-lens-multi-compare | vis-lens-chart-select |
| instrument_validation | vis-lens-multi-compare | vis-lens-uncertainty |
| observational_correlational | vis-lens-chart-select | vis-lens-figure-table |
| qualitative_interpretive | vis-lens-chart-select | vis-lens-uncertainty |
| robustness_audit | vis-lens-chart-select | vis-lens-uncertainty |
| simulation_modeling | vis-lens-temporal | vis-lens-chart-select |
| single_subject | vis-lens-chart-select | vis-lens-uncertainty |
| (default) | vis-lens-chart-select | — |

Cap Tier B at 2 lenses total (overrides count toward this cap).

**Tier C (0–1 based on methodology tradition detection):**

Tier C selects `vis-lens-methodology-norms` when the experiment plan's research
methodology is identifiable from the 12 bundled methodology traditions.

**Stage 1 — Deterministic keyword match:**
1. Load all methodology traditions from `recipes/methodology-traditions/*.yaml`
2. For each tradition, count how many of its `detection_keywords` appear in the
   experiment plan text (case-insensitive, word-boundary matching)
3. Build `candidate_set` = traditions with ≥ 2 keyword matches
4. Branch on `len(candidate_set)`:

| Candidates | Action | `precedence_trace` |
|---|---|---|
| 0 | Skip Tier C entirely | `stage1_no_match_fallback` |
| 1 | Use that tradition as `primary_tradition` | `stage1_single_match` |
| ≥ 2 | Proceed to Stage 2 | — |

**Stage 2 — LLM arbitration (only when ≥ 2 candidates):**
1. If any registered `UnionRuleDef` covers the candidate set, apply it:
   select `primary_tradition`, record rule name in `applied_union_rules`,
   set `precedence_trace = "stage2_tiebreak_by_rule_{rule_name}"`
2. Otherwise, select among candidates by analyzing the plan's primary research
   question and methodology at temperature 0. Prefer the tradition whose
   mandatory figures are most relevant to the stated research design.
   Set `precedence_trace = "stage2_tiebreak_by_methodology_fit"`

**Emit routing triple** (include as a fenced yaml block in the vis-lens context file):
```yaml
primary_tradition: <tradition_slug>
applied_union_rules: [<rule_name>, ...]
precedence_trace: "<trace_value>"
candidate_set: [<tradition_slugs>]
```

When `primary_tradition` is set, add `vis-lens-methodology-norms` to `selected_lenses`
and write the `tradition_slug` and routing triple into its context file (Step 2).

Only add Tier C lens if it is not already in Tier A or Tier B.

**Enforcement:** Total must be 2–4. If total < 2, add `vis-lens-chart-select`. If total
> 4, drop the last Tier C lens, then last Tier B secondary.

### Step 2 — Write Vis-Lens Context Files

For each lens in `selected_lenses`, write a context file:

Path: `{{AUTOSKILLIT_TEMP}}/select-vis-lenses/vis_ctx_{slug}_{YYYY-MM-DD_HHMMSS}.md`

Template for each context file:
```
# Vis-Lens Context: {slug}

## Experiment Summary
{1–3 sentence description of the experiment from the plan}

## Data Shape
- Dependent Variables ({num_DVs} total): {DV names and types}
- Independent Variables: {IV names, levels, and ranges}
- Conditions: {num_conditions} conditions
- Replication: {n_seeds or n_trials if available}

## DV Specification
{For each DV: name, type (continuous/discrete/temporal), unit, expected range}

## IV Specification
{For each IV: name, type, levels (for categorical) or range (for continuous)}

## Comparison Structure
- Comparative: {true/false}
- Head-to-head pairs: {list if applicable}
- Factorial interactions: {list if applicable}

## Expected Data Outputs
{List the files or data structures the experiment will produce, from the plan's
data_manifest or results/ section if available}
```

When the context file is for `vis-lens-methodology-norms`, append the following
section to the template above:

```
## Methodology Tradition
~~~yaml
primary_tradition: {slug}
applied_union_rules: [{rules}]
precedence_trace: {trace}
candidate_set: [{candidates}]
~~~
```

### Step 3 — Emit Structured Tokens

Emit exactly five tokens as your final output:

```
selected_lenses = {comma-separated list of vis-lens skill slugs}
lens_context_paths = {comma-separated list of absolute paths to context files}
disambiguation_rule_applied = {first rule_name from applied_union_rules if list is non-empty, else null}
tier_c_lens = {vis-lens-methodology-norms or null}
methodology_tradition = {primary_tradition slug or null}
```

Do NOT emit `visualization_plan_path`, `report_plan_path`, `visualization_plan_trace_path`,
or `classification_timestamp` — those belong to `synthesize-vis-plan` or other downstream steps.
