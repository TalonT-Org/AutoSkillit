---
name: planner-refine-phases
categories: [planner]
description: Refine elaborated phases with cross-phase visibility via parallel L0 subagents (L1+L0 pattern)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-refine-phases] Refining phases with cross-visibility...'"
          once: true
---

# planner-refine-phases

L1 session that refines a `combined_plan.json` (a `PlanDocument` with every phase
in `PhaseElaborated` form) by spawning one L0 subagent per phase in parallel.
Each L0 reviews its assigned phase in the context of all other phases and returns
structured suggestions. L1 collects these suggestions, resolves inter-phase
conflicts, applies field-level edits to the plan, and writes `refined_plan.json`.

## When to Use

- Launched by the L2 planner recipe after the parallel phase elaboration merge step
- Accepts the `combined_plan.json` output from the parallel phase elaboration merge step
- Produces `refined_plan.json` as input for downstream planner steps

## Arguments

- **$1** — Absolute path to `combined_plan.json` (PlanDocument, every phase as PhaseElaborated)
- **$2** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`). Output is written to `$2/refined_plan.json`.

## Critical Constraints

**NEVER:**
- Write any file outside `$2/`
- Directly modify the combined_plan.json ($1) — always write a new refined_plan.json
- Allow an L0 subagent to write files directly (L0s return structured text only)
- Emit `refined_plan_path` before writing `refined_plan.json`
- Skip emitting `refined_plan_path` even if all L0s fail (write unchanged plan, still emit)
- Read `{{AUTOSKILLIT_TEMP}}` artifacts not passed as positional arguments
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Validate each L0 response for `phase_id`, `changes` (array), `conflicts` (array)
- Log a `WARNING` to stdout for any L0 response that fails validation (skip that phase)
- Log `CRITICAL` to stdout for any L0 subagent that fails entirely (proceed with N-1)
- Log each conflict resolution to stdout before applying it
- Emit: `refined_plan_path = <absolute path to refined_plan.json>`

## Workflow

### Step 1: Parse input and validate

Read `$1` (combined_plan.json). Parse as a `PlanDocument`. Extract all phase IDs
from `phases[*].id`. Fail immediately (exit non-zero) if `phases` is empty or the
file is malformed — do not proceed to spawn L0s.

Input schema (PlanDocument with PhaseElaborated phases):
```json
{
  "schema_version": 1,
  "task": "...",
  "source_dir": "...",
  "phases": [
    {
      "id": "P1",
      "name": "...",
      "goal": "...",
      "scope": [...],
      "ordering": 1,
      "technical_approach": "...",
      "relationship_notes": "...",
      "assignments_preview": [...]
    }
  ]
}
```

### Step 2: Spawn parallel L0 subagents

Read the `task` field from the combined plan document. Each L0 subagent reviewing a phase
must verify that the phase's goal and scope serve the stated task. Phases that appear to
address codebase concerns not mentioned in the task should be flagged for scope creep.

Spawn one L0 subagent per phase in parallel using the Agent/Task tool. Each L0 receives:
- The full serialized `combined_plan.json` content (pasted inline or via file read)
- Its `target_phase_id` (e.g., `"P2"`)
- Instructions: review the target phase in light of what all other phases committed
  to; return structured suggestions only — do NOT edit files

Each L0 must return structured text in this exact format:
```
phase_id = P2
changes = [
  {"field": "relationship_notes", "new_value": "Depends on P1 for the auth client interface defined in P1-A2-WP1"},
  {"field": "technical_approach", "new_value": "...updated approach..."}
]
conflicts = [
  {"with_phase": "P1", "description": "Both P1 and P2 claim ownership of the token refresh module"}
]
```

### Step 3: Validate L0 responses

For each L0 response:
- `phase_id` must be present and match the expected phase ID for that L0
- `changes` must be a valid JSON array (may be empty `[]`)
- `conflicts` must be a valid JSON array (may be empty `[]`)

On validation failure:
```
WARNING: L0 response for {phase_id} failed validation — skipping
```

On L0 subagent complete failure (no response / timeout):
```
CRITICAL: L0 for {phase_id} failed — proceeding with N-1 suggestions
```

### Step 4: Collect and resolve conflicts

Collect all `conflicts` arrays from validated L0 responses. Group conflict reports
by the pair of phases involved. When multiple L0s report conflicts involving the
same resource:
1. Read the conflict descriptions
2. Make an ownership/scope judgment call based on phase goals and scopes
3. Log resolution before applying:
   ```
   CONFLICT RESOLUTION: {phase_id} vs {with_phase} — {resolution decision}
   ```

### Step 5: Apply changes

Apply all validated `changes` to the in-memory plan document, in phase ID order
(P1, P2, ... PN). Apply conflict resolutions before applying changes for the
affected phases. Only update fields that exist in `PhaseElaborated` — skip
unrecognized field names:
```
WARNING: Unrecognized field '{field}' in changes for {phase_id} — skipping
```

### Step 6: Write output

Write the updated plan document to `$2/refined_plan.json`. The output schema is
identical to the input `combined_plan.json` (a `PlanDocument` with
`phases: list[PhaseElaborated]`).

### Step 7: Emit output token

```
refined_plan_path = <absolute path to $2/refined_plan.json>
```
