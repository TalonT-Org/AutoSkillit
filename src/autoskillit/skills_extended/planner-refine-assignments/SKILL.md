---
name: planner-refine-assignments
categories: [planner]
description: Refine elaborated assignments for a single phase via parallel L0 subagents (L1+L0 pattern), using per-phase context file with peer_summaries for cross-phase visibility
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-refine-assignments] Refining phase assignments with cross-visibility...'"
          once: true
---

# planner-refine-assignments

L1 session that refines the assignments for a single phase by spawning one L0 subagent per
assignment in parallel (3–5 assignments per phase). Receives a per-phase context file produced
by `merge_tier_results` → `_write_refine_contexts`, which contains only this phase's assignments
and `peer_summaries` (id/name/goal stubs) for all other phases. Each L0 reviews its assignment
in the context of peer_summaries and `refined_plan.json`, returning structured suggestions. L1
applies suggestions, resolves intra-phase WP ownership conflicts, and writes the phase result
file to `$3/refine_contexts/{phase_id}_result.json`.

## When to Use

- Dispatched by the L2 planner recipe in parallel — one session per phase
- Accepts a per-phase context file from `$3/refine_contexts/context_{phase_id}.json` and `refined_plan.json` for phase context
- Produces `{phase_id}_result.json` as input for the downstream `merge_refined_assignments` step

## Arguments

- **$1** — Absolute path to the per-phase context file (`$3/refine_contexts/context_{phase_id}.json`). The file contains:
  - `phase_id` — identifier for the phase this session processes
  - `task_file_path` — path to the task description file (read from disk, not inline)
  - `assignments` — full AssignmentElaborated objects for this phase only (3–5 entries)
  - `peer_summaries` — `{id, name, goal}` stubs for all assignments in other phases
- **$2** — Absolute path to `refined_plan.json` (PlanDocument with phases as PhaseElaborated, for phase context)
- **$3** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`). Output is written to `$3/refine_contexts/{phase_id}_result.json`.

## Critical Constraints

**NEVER:**
- Write any file outside `$3/`
- Directly modify the context file ($1) — always write a new result file
- Allow an L0 subagent to write files directly (L0s return structured text only)
- Emit `phase_refined_path` before writing the result file
- Skip emitting `phase_refined_path` even if all L0s fail (write unchanged assignments, still emit)
- Spawn more than 6 L0s in a single parallel batch
- Read `{{AUTOSKILLIT_TEMP}}` artifacts not passed as positional arguments
- Run subagents in the background (`run_in_background: true` is prohibited)
- Write L0 prompts to intermediate `l0_prompts/` files and read them back into the L1 context — spawn L0 subagents directly from in-memory context packets

**ALWAYS:**
- Read `phase_id` from the context file to construct the output path
- Validate each L0 response for `assignment_id`, `changes` (array), `dependency_corrections` (array), `wp_proposal_adjustments` (array)
- Log `WARNING` to stdout for any L0 response that fails validation (skip that assignment)
- Log `CRITICAL` to stdout for any L0 subagent that fails entirely (proceed with N-1, partial result)
- When two assignments propose WPs covering the same files, assign ownership to the numerically earlier assignment_id using natural sort on numeric suffixes (e.g. `P1-A1` beats `P1-A2`; `P1-A2` beats `P1-A10`); log each resolution
- Emit: `phase_refined_path = <absolute path to $3/refine_contexts/{phase_id}_result.json>`

## Workflow

### Step 1: Parse input and validate

Read `$1` (per-phase context file). Extract `phase_id`, `assignments`, `peer_summaries`, and
`task_file_path`. Fail immediately (exit non-zero) if `assignments` is empty or the file is
malformed — do not proceed to spawn L0s. The failure message must include the file path and the
parse/validation error string:
```
FATAL: failed to parse {path}: {error_detail}
```

Read `$2` (refined_plan.json). Build a map `phase_id → PhaseElaborated` for L0 context.

Input schema for the per-phase context file:
```json
{
  "phase_id": "P2",
  "task_file_path": "/path/to/task.md",
  "assignments": [
    {
      "id": "P2-A1",
      "phase_id": "P2",
      "name": "...",
      "goal": "...",
      "technical_approach": "...",
      "dependency_notes": "...",
      "work_packages": [...]
    }
  ],
  "peer_summaries": [
    {"id": "P1-A1", "name": "...", "goal": "..."},
    {"id": "P3-A1", "name": "...", "goal": "..."}
  ]
}
```

### Step 2: Build L0 context packets

For each assignment in `assignments`, build a context packet containing:
- The full serialized assignment object (AssignmentElaborated)
- The `peer_summaries` list for cross-phase dependency detection
- The `PhaseElaborated` entry for the assignment's `phase_id` from `$2`
- The `target_assignment_id`
- `task_file_path` — the path to the task description on disk (pass the path reference only; do NOT read the task text into the L1 context or embed it in the L0 prompt)
- Instructions: review the target assignment in light of peer_summaries; if scope creep verification is needed, read the task from disk at `task_file_path`; return structured suggestions only — do NOT edit files

### Step 3: Spawn parallel L0 subagents

Since each phase has 3–5 assignments (always within the 6-L0 ceiling), spawn all in one
parallel batch via Agent/Task. Do not spawn more than 6 L0s in a single parallel batch:
if assignment count > 6 (unexpected), spawn sequential batches of 6. Between batches, emit
anti-prose guard line: `--- next batch ---`.

Spawn each L0 directly from its in-memory context packet — do NOT write the L0 prompts to
`l0_prompts/*.txt` files and read them back. Reading those files before spawning adds ~15K
tokens to the L1 context per L0 with no benefit.

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
ID order. Apply conflict resolutions before applying changes for affected assignments.
Skip unrecognized field names:
```
WARNING: Unrecognized field '{field}' in changes for {assignment_id} — skipping
```
Apply `dependency_corrections` by appending to the `dependency_notes` field.

### Step 7: Write output

Write the phase result file to `$3/refine_contexts/{phase_id}_result.json`, where
`phase_id` is read from the context file. The output schema:
```json
{
  "schema_version": 1,
  "assignments": [...]
}
```
The `assignments` list contains only this phase's assignments (3–5 entries) with refinements applied.

### Step 8: Emit output token

```
phase_refined_path = <absolute path to $3/refine_contexts/{phase_id}_result.json>
```
