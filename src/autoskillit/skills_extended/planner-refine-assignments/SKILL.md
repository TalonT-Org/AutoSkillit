---
name: planner-refine-assignments
categories: [planner]
description: Refine elaborated assignments with cross-assignment visibility via parallel L0 subagents (L1+L0 pattern)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-refine-assignments] Refining assignments with cross-visibility...'"
          once: true
---

# planner-refine-assignments

L1 session that refines a `combined_assignments.json` (a `PlanDocument` with all
assignments in `AssignmentElaborated` form) by spawning one L0 subagent per
assignment in parallel (batched to 6). Each L0 reviews its assigned assignment in
the context of all other assignments and the phase context from `refined_plan.json`,
returning structured suggestions. L1 collects these suggestions, resolves
cross-assignment WP ownership conflicts, applies field-level edits, and writes
`refined_assignments.json`.

## When to Use

- Launched by the L2 planner recipe after the parallel assignment elaboration merge step
- Accepts the `combined_assignments.json` output from the merge step and `refined_plan.json` for phase context
- Produces `refined_assignments.json` as input for downstream planner steps

## Arguments

- **$1** — Absolute path to `combined_assignments.json` (PlanDocument, all assignments as AssignmentElaborated)
- **$2** — Absolute path to `refined_plan.json` (PlanDocument with phases as PhaseElaborated, for phase context)
- **$3** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`). Output is written to `$3/refined_assignments.json`.

## Critical Constraints

**NEVER:**
- Write any file outside `$3/`
- Directly modify `combined_assignments.json` ($1) — always write a new `refined_assignments.json`
- Allow an L0 subagent to write files directly (L0s return structured text only)
- Emit `refined_assignments_path` before writing `refined_assignments.json`
- Skip emitting `refined_assignments_path` even if all L0s fail (write unchanged assignments, still emit)
- Spawn more than 6 L0s in a single parallel batch
- Read `{{AUTOSKILLIT_TEMP}}` artifacts not passed as positional arguments

**ALWAYS:**
- Validate each L0 response for `assignment_id`, `changes` (array), `dependency_corrections` (array), `wp_proposal_adjustments` (array)
- Log `WARNING` to stdout for any L0 response that fails validation (skip that assignment)
- Log `CRITICAL` to stdout for any L0 subagent that fails entirely (proceed with N-1)
- When two assignments propose WPs covering the same files, assign ownership to the numerically earlier assignment_id using natural sort on numeric suffixes (e.g. `P1-A1` beats `P1-A2`; `P1-A2` beats `P1-A10`); log each resolution
- Emit: `refined_assignments_path = <absolute path to refined_assignments.json>`

## Workflow

### Step 1: Parse input and validate

Read `$1` (combined_assignments.json). Parse as a `PlanDocument`. Extract all
assignment IDs from `assignments[*].id`. Fail immediately (exit non-zero) if
`assignments` is empty or the file is malformed — do not proceed to spawn L0s.
The failure message must include the file path and the parse/validation error string:
```
FATAL: failed to parse {path}: {error_detail}
```

Read `$2` (refined_plan.json). Build a map `phase_id → PhaseElaborated` for L0 context.

Input schema (PlanDocument with AssignmentElaborated assignments):
```json
{
  "schema_version": 1,
  "task": "...",
  "source_dir": "...",
  "assignments": [
    {
      "id": "P1-A1",
      "phase_id": "P1",
      "name": "...",
      "goal": "...",
      "technical_approach": "...",
      "dependency_notes": "...",
      "work_packages": [...]
    }
  ]
}
```

### Step 2: Build L0 context packets

Read the `task` field from the combined assignments document. Each L0 subagent must verify
that the assignment's goal, scope, and deliverables serve the stated task. Flag assignments
that introduce work not requested by the task as scope creep.

For each assignment, build a context packet containing:
- The full serialized `combined_assignments.json` content (all peers visible)
- The `PhaseElaborated` entry for the assignment's `phase_id` from `$2`
- The `target_assignment_id`
- Instructions: review the target assignment in light of all other assignments; return structured suggestions only — do NOT edit files

### Step 3: Spawn parallel L0 subagents

If assignment count ≤ 6: spawn all in one parallel batch via Agent/Task.
If assignment count > 6: spawn sequential batches of 6. Between batches, emit
anti-prose guard line: `--- next batch ---`.

Each L0 must return structured text in this exact format:
```
assignment_id = P1-A2
changes = [
  {"field": "dependency_notes", "new_value": "Requires P1-A1-WP1 interface before starting"},
  {"field": "technical_approach", "new_value": "..."}
]
dependency_corrections = [
  {"missing_dep": "P1-A1-WP2", "reason": "Uses the auth token cache defined there"}
]
wp_proposal_adjustments = [
  {"wp_id_suffix": "WP1", "action": "remove", "reason": "Duplicate of P1-A1-WP3 which owns users.py"}
]
```

### Step 4: Validate L0 responses

For each L0 response:
- `assignment_id` must be present and match the expected assignment ID for that L0
- `changes` must be a valid JSON array (may be empty `[]`)
- `dependency_corrections` must be a valid JSON array (may be empty `[]`)
- `wp_proposal_adjustments` must be a valid JSON array (may be empty `[]`)

On `assignment_id` mismatch (field present but does not match expected ID):
```
WARNING: L0 response assignment_id mismatch — expected {expected}, got {actual} — skipping
```

On other validation failure (field absent or array invalid):
```
WARNING: L0 response for {assignment_id} failed validation — skipping
```

On L0 subagent complete failure (no response / timeout):
```
CRITICAL: L0 for {assignment_id} failed — proceeding with N-1 suggestions
```

### Step 5: Resolve WP proposal conflicts

Collect all `wp_proposal_adjustments` from validated L0 responses. Group by the
set of files each WP proposal covers (use `estimated_files` from the elaborated WP).
When two assignments both claim a file set: assign ownership to the assignment with
the numerically earlier `assignment_id` using natural sort on numeric suffixes
(e.g. `P1-A1` beats `P1-A2`; `P1-A2` beats `P1-A10`).
Log each resolution:
```
WP CONFLICT: {assignment_id_A} vs {assignment_id_B} — {field} assigned to {winner}
```
Apply the `remove` action from the losing assignment's `wp_proposal_adjustments`.

### Step 6: Apply changes

Apply all validated `changes` to the in-memory assignments document, in assignment
ID order (P1-A1 → P1-AN → P2-A1 ...). Apply conflict resolutions before applying
changes for affected assignments. Skip unrecognized field names:
```
WARNING: Unrecognized field '{field}' in changes for {assignment_id} — skipping
```
Apply `dependency_corrections` by appending to the `dependency_notes` field.

### Step 7: Write output

Write the updated `PlanDocument` to `$3/refined_assignments.json`. The output
schema is identical to the input `combined_assignments.json` (a `PlanDocument`
with `assignments: list[AssignmentElaborated]`).

### Step 8: Emit output token

```
refined_assignments_path = <absolute path to $3/refined_assignments.json>
```
