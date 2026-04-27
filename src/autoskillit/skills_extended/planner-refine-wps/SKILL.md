---
name: planner-refine-wps
categories: [planner]
description: Refine elaborated work packages with cross-phase visibility via per-phase L0 subagents (L1+L0 pattern)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-refine-wps] Refining WPs with cross-phase visibility...'"
          once: true
---

# planner-refine-wps

L1 session that refines a `combined_wps.json` (a `PlanDocument` with all work
packages in `WPElaborated` form) by spawning one L0 subagent per phase in
parallel (batched to 6). Unlike the per-WP elaboration pass, this skill spawns
one L0 per **phase** (not per WP). Each L0 reviews ALL WPs in its assigned phase
against the full WP set, detecting cross-phase API mismatches, duplicate
deliverables, missing dependencies, and scope overlap. The L1 collects structured
suggestions, resolves conflicts, and writes `refined_wps.json`.

## When to Use

- Launched by the L2 planner recipe after the parallel WP elaboration merge step
- Accepts `combined_wps.json`, `refined_plan.json`, and `refined_assignments.json`
- Produces `refined_wps.json` as input for downstream planner steps (e.g. reconcile_deps)

## Arguments

- **$1** — Absolute path to `combined_wps.json` (PlanDocument with `work_packages: list[WPElaborated]`)
- **$2** — Absolute path to `refined_plan.json` (PlanDocument with phases as PhaseElaborated)
- **$3** — Absolute path to `refined_assignments.json` (PlanDocument with assignments as AssignmentElaborated)
- **$4** — Absolute path to output directory for `refined_wps.json` (must exist under `{{AUTOSKILLIT_TEMP}}/planner/`)

## Critical Constraints

**NEVER:**
- Write any file outside `{{AUTOSKILLIT_TEMP}}/planner/`
- Directly modify `combined_wps.json` ($1) — always write a new `refined_wps.json`
- Allow an L0 subagent to write files directly (L0s return structured text only)
- Emit `refined_wps_path` before writing `refined_wps.json`
- Skip emitting `refined_wps_path` even if all L0s fail (write unchanged WPs, still emit)
- Spawn more than 6 L0s in a single parallel batch
- Spawn one L0 per WP — L0s operate per PHASE

**ALWAYS:**
- Spawn one L0 per phase (NOT per WP) — each L0 reviews ALL WPs in its phase against the full WP set
- Validate each L0 response for `phase_id`, `wp_changes` (array), `cross_phase_deps` (array), `deliverable_conflicts` (array), `api_mismatches` (array)
- Log `WARNING` to stdout for any L0 response that fails validation (skip that phase)
- Log `CRITICAL` to stdout for any L0 subagent that fails entirely (proceed with N-1 suggestions)
- When two WPs claim the same deliverable file, assign ownership to the WP with the numerically earlier ID using natural sort (e.g., `P1-A1-WP1` beats `P2-A1-WP1`)
- Emit: `refined_wps_path = <absolute path to refined_wps.json>`

## Workflow

### Step 1: Parse inputs and validate

Read `$1` (combined_wps.json). Parse as a `PlanDocument`. Extract all WP entries
from `work_packages[]`. Fail immediately (exit non-zero) if `work_packages` is
empty or the file is malformed — do not proceed to spawn L0s. The failure message
must include the file path and the parse/validation error string:
```
FATAL: failed to parse {path}: {error_detail}
```

Read `$2` (refined_plan.json). Build a map `phase_id → PhaseElaborated` for phase context.
Read `$3` (refined_assignments.json). Build a map `assignment_id → AssignmentElaborated` for assignment context.

Input schema (PlanDocument with WPElaborated work packages):
```json
{
  "schema_version": 1,
  "task": "...",
  "source_dir": "...",
  "work_packages": [
    {
      "id": "P1-A1-WP1",
      "assignment_id": "P1-A1",
      "phase_id": "P1",
      "name": "...",
      "scope": "...",
      "estimated_files": ["..."],
      "goal": "...",
      "summary": "...",
      "technical_steps": ["..."],
      "files_touched": ["..."],
      "apis_defined": ["..."],
      "apis_consumed": ["..."],
      "depends_on": ["..."],
      "deliverables": ["..."],
      "acceptance_criteria": ["..."]
    }
  ]
}
```

### Step 2: Group WPs by phase and build L0 context packets

Group WPs by `phase_id` (extracted from `id` prefix, e.g., `P1-A1-WP1` → `P1`).
For each phase, build a context packet containing:
- The full serialized `combined_wps.json` content (all WPs visible for cross-phase awareness)
- The `PhaseElaborated` entry for the phase from `$2`
- The `AssignmentElaborated` entries for all assignments in this phase from `$3`
- The `target_phase_id`
- The list of WP IDs assigned to this L0 (the WPs in this phase)
- Instructions: review this phase's WPs against the full WP set; return structured suggestions only — do NOT edit files

### Step 3: Spawn parallel L0 subagents

If phase count ≤ 6: spawn all in one parallel batch via Agent/Task.
If phase count > 6: spawn sequential batches of 6. Between batches, emit
anti-prose guard line: `--- next batch ---`.

Each L0 MUST return structured text in this exact format:
```
phase_id = P1
wp_changes = [
  {"wp_id": "P1-A1-WP1", "field": "depends_on", "new_value": ["P2-A1-WP3"]},
  {"wp_id": "P1-A1-WP2", "field": "technical_steps", "new_value": ["Step 1...", "Step 2..."]}
]
cross_phase_deps = [
  {"wp_id": "P1-A1-WP2", "missing_dep": "P2-A1-WP1", "reason": "Consumes auth_client API defined by P2-A1-WP1"}
]
deliverable_conflicts = [
  {"wp_id_a": "P1-A1-WP1", "wp_id_b": "P2-A1-WP3", "file": "src/auth/client.py"}
]
api_mismatches = [
  {"consumer_wp": "P2-A1-WP1", "producer_wp": "P1-A1-WP2", "api": "SessionModel.create", "mismatch": "Consumer expects (user_id, token) but producer defines (user_id)"}
]
```

Each L0 receives instructions to use Grep/Glob/Read for codebase analysis but NOT
to write files or spawn sub-subagents.

### Step 4: Validate L0 responses

For each L0 response:
- `phase_id` must be present and match the expected phase ID
- `wp_changes` must be a valid JSON array (may be empty `[]`)
- `cross_phase_deps` must be a valid JSON array (may be empty `[]`)
- `deliverable_conflicts` must be a valid JSON array (may be empty `[]`)
- `api_mismatches` must be a valid JSON array (may be empty `[]`)

On `phase_id` mismatch (field present but does not match expected ID):
```
WARNING: L0 response phase_id mismatch — expected {expected}, got {actual} — skipping
```

On other validation failure (field absent or array invalid):
```
WARNING: L0 response for {phase_id} failed validation — skipping
```

On L0 subagent complete failure (no response / timeout):
```
CRITICAL: L0 for {phase_id} failed — proceeding with N-1 suggestions
```

### Step 5: Resolve conflicts

Collect all `deliverable_conflicts` from validated L0 responses. For each
conflict where two WPs claim the same deliverable file, assign ownership to the
WP with the numerically earlier ID (natural sort: `P1-A1-WP1` < `P1-A2-WP1` <
`P2-A1-WP1`). Log each resolution:
```
WP CONFLICT: {wp_id_a} vs {wp_id_b} — deliverable {file} assigned to {winner}
```

Process `api_mismatches`: for each mismatch, add the producer WP's `apis_defined`
signature to the `wp_changes` for the consumer WP to update `apis_consumed` to match.

Process `cross_phase_deps`: for each missing dependency, append to the target WP's
`depends_on` list.

### Step 6: Apply changes

Apply all validated `wp_changes` to the in-memory WPs document, in WP ID order
(P1-A1-WP1 → P1-A1-WP2 → ... → PN-AN-WPN). Apply conflict resolutions and
cross-phase dep corrections before field-level changes. Skip unrecognized field names:
```
WARNING: Unrecognized field '{field}' in wp_changes for {wp_id} — skipping
```

Valid WPElaborated fields for changes: `goal`, `summary`, `technical_steps`,
`files_touched`, `apis_defined`, `apis_consumed`, `depends_on`, `deliverables`,
`acceptance_criteria`, `scope`, `estimated_files`.

### Step 7: Write output

Write the updated `PlanDocument` to `$4/refined_wps.json`. The output schema is
identical to the input `combined_wps.json` (a `PlanDocument` with
`work_packages: list[WPElaborated]`).

### Step 8: Emit output token

```
refined_wps_path = <absolute path to $4/refined_wps.json>
```
