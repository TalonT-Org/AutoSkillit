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

L1 session that refines a single phase's work packages (from a per-phase context
file produced by `merge_wps`) by spawning one L0 subagent per phase in parallel
(batched to 6). Unlike the per-WP elaboration pass, this skill spawns one L0 per
**phase** (not per WP). Each L0 reviews ALL WPs in its assigned phase against peer
WP stubs for cross-phase awareness, detecting API mismatches, duplicate deliverables,
missing dependencies, and scope overlap. The L1 collects structured suggestions,
resolves conflicts, and writes `{phase_id}_result.json` to the `wp_refine_contexts/`
directory.

## When to Use

- Launched by the L2 planner recipe once per phase after the WP merge step
- Accepts a per-phase context file from `wp_refine_contexts/`, `refined_plan.json`, and planner_dir
- Produces `{phase_id}_result.json` as input for `merge_refined_wps`

## Arguments

- **$1** — Absolute path to per-phase context file (`wp_refine_contexts/context_{phase_id}.json`)
- **$2** — Absolute path to `refined_plan.json` (PlanDocument with phases as PhaseElaborated)
- **$3** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`). Output is written to `$3/wp_refine_contexts/{phase_id}_result.json`.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Write any file outside `$3/`
- Directly modify the context file ($1) — always write a new `{phase_id}_result.json`
- Allow an L0 subagent to write files directly (L0s return structured text only)
- Emit `phase_wp_refined_path` before writing `{phase_id}_result.json`
- Skip emitting `phase_wp_refined_path` even if all L0s fail (write unchanged WPs, still emit)
- Spawn more than 6 L0s in a single parallel batch
- Spawn one L0 per WP — L0s operate per PHASE
- Read `{{AUTOSKILLIT_TEMP}}` artifacts not passed as positional arguments
- Run subagents in the background (`run_in_background: true` is prohibited)

- Write, Edit, or use file-modifying Bash commands (sed -i, echo >, tee) on any file outside the planner output directory ($AUTOSKILLIT_ALLOWED_WRITE_PREFIX). Source code files must NEVER be modified.

**ALWAYS:**
- Spawn one L0 per phase (NOT per WP) — each L0 reviews ALL WPs in its phase against the full WP set
- Validate each L0 response for `phase_id`, `wp_changes` (array), `cross_phase_deps` (array), `deliverable_conflicts` (array), `api_mismatches` (array), `subsumption_pairs` (array)
- Log `WARNING` to stdout for any L0 response that fails validation (skip that phase)
- Log `CRITICAL` to stdout for any L0 subagent that fails entirely (proceed with N-1 suggestions)
- When two WPs claim the same deliverable file, assign ownership to the WP with the numerically earlier ID using natural sort (e.g., `P1-A1-WP1` beats `P2-A1-WP1`)
- Emit: `phase_wp_refined_path = <absolute path to $3/wp_refine_contexts/{phase_id}_result.json>`

## Workflow

### Step 1: Parse inputs and validate

Read `$1` (per-phase context file: `wp_refine_contexts/context_{phase_id}.json`). Extract:
- `phase_id` — the phase this session handles
- `work_packages` — full `WPElaborated` objects for this phase
- `peer_summaries` — stub dicts for WPs in all other phases
- `task_file_path` — path to the task document

Fail immediately (exit non-zero) if `work_packages` is empty or the file is malformed:
```
FATAL: failed to parse {path}: {error_detail}
```

Read `$2` (refined_plan.json). Build a map `phase_id → PhaseElaborated` for phase context.
The `$3` argument is the planner output directory; output is written to `$3/wp_refine_contexts/{phase_id}_result.json`.

Input schema (per-phase context file):
```json
{
  "schema_version": 1,
  "phase_id": "P1",
  "task_file_path": "/path/to/task.md",
  "work_packages": [
    {
      "id": "P1-A1-WP1",
      "assignment_id": "P1-A1",
      "phase_id": "P1",
      "name": "...",
      "scope": "...",
      "technical_steps": ["..."],
      "files_touched": ["..."],
      "apis_defined": ["..."],
      "apis_consumed": ["..."],
      "depends_on": ["..."],
      "deliverables": ["..."],
      "acceptance_criteria": ["..."]
    }
  ],
  "peer_summaries": [
    {
      "id": "P2-A1-WP1",
      "name": "...",
      "scope": "...",
      "deliverables": ["..."],
      "apis_defined": ["..."],
      "apis_consumed": ["..."]
    }
  ]
}
```

### Step 2: Build L0 context packets per phase

Read `task_file_path` from the context file to load the task description. Each L0 subagent
reviews this phase's WPs against peer stubs for cross-phase awareness. Flag WPs whose
deliverables address concerns not in the task as scope creep.

For each phase (one context file = one phase), build a context packet containing:
- The full `work_packages` list from the context file (own phase's WPs in detail)
- The `peer_summaries` list from the context file (other phases' WPs as stubs)
- The `PhaseElaborated` entry for this phase from `$2`
- The `target_phase_id` (from `context.phase_id`)
- Instructions: review this phase's WPs against peer stubs; return structured suggestions only — do NOT edit files
- Use `overlap_notes` from the PhaseElaborated entry as a prior signal for subsumption detection

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
subsumption_pairs = [
  {"consumer_wp": "P6-A3-WP1", "subsumed_wp": "P8-A5-WP1", "reason": "WP1 implements build_interactive_cmd and will naturally produce tests; WP2's sole purpose is to add those tests"}
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
- `subsumption_pairs` must be a valid JSON array (may be empty `[]`)

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

Collect all `subsumption_pairs` from validated L0 responses. For each pair:
1. Promote the subsumed WP's unique deliverables to the consumer WP (those not already in the consumer's list)
2. Append the subsumed WP's unique acceptance criteria to the consumer WP
3. Remove the subsumed WP from the output WP list
4. Update all `depends_on` references: any WP that depended on the subsumed WP should instead depend on the consumer WP
5. Write voided_wps entry to `$4/work_packages/lifecycle_registry.json`:
   Read existing registry (or create with defaults `{"voided_phases": [], "voided_assignments": [], "absorbed": {}, "voided_wps": {}}`).
   Add to `voided_wps`: `{subsumed_id: {"merged_into": consumer_id, "reason": reason}}`.
   Write back with `schema_version: 1`.
6. Log: `WP SUBSUMED: {subsumed_id} → {consumer_id} ({reason})`

Collect all `deliverable_conflicts` from validated L0 responses. For each
conflict where two WPs claim the same deliverable file, assign ownership to the
WP with the numerically earlier ID (natural sort: `P1-A1-WP1` < `P1-A2-WP1` <
`P2-A1-WP1`). Log each resolution:
```
WP CONFLICT: {wp_id_a} vs {wp_id_b} — deliverable {file} assigned to {winner}
```

**Post-deduplication orphan check:** After resolving all deliverable conflicts,
scan every losing WP. If any WP now has `deliverables: []`, merge it into the
winning WP (the one that received its deliverables):
1. Promote the orphan's `files_touched` entries as deliverables to the winner WP,
   selecting at most `DELIVERABLE_BOUNDS[1] - len(winner.deliverables)` entries
   (prefer entries that overlap with the winner's existing scope). Any remaining
   entries stay in `files_touched` only.
2. Append the orphan's `technical_steps` and `acceptance_criteria` to the winner
3. Remove the orphan WP from the output and update all `depends_on` references
4. Log: `WP ORPHAN MERGED: {orphan_id} → {winner_id}`
Deliverable count bounds are defined in `schema.py::DELIVERABLE_BOUNDS`.

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

Write the updated work packages for this phase to `$3/wp_refine_contexts/{phase_id}_result.json`.
The output schema:
```json
{
  "schema_version": 1,
  "work_packages": [ ... refined WPElaborated objects for this phase ... ]
}
```

### Step 8: Emit output token

```
phase_wp_refined_path = <absolute path to $3/wp_refine_contexts/{phase_id}_result.json>
```
