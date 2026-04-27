---
name: planner-elaborate-phase
categories: [planner]
description: Elaborate a single phase into a full result, parallel-safe — receives plan snapshot + target phase ID
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-elaborate-phase] Elaborating phase...'"
          once: true
---

# planner-elaborate-phase

Standalone parallel worker for Phase Pass 1. Each instance receives the full plan
snapshot (each phase in condensed form) and a target phase ID. It explores the codebase
independently and writes a single elaborated phase result. No dependency on
`check_remaining` or any shared state machine.

## When to Use

- Launched in parallel by the L2 orchestrator (planner recipe, Issue 08)
- One instance per phase ID, all running simultaneously
- Also usable standalone for manual single-phase elaboration

## Arguments

- **$1** — Absolute path to `plan_snapshot.json` (every phase as a `PhaseShort` entry)
- **$2** — Phase ID to elaborate (e.g., `"P3"`)
- **$3** — Absolute path to output directory (result written here)

## Critical Constraints

**NEVER:**
- Write output outside `$3/`
- Read any `*_result.json` file from other phases (you have only the snapshot)
- Require or read a context file from `check_remaining`
- Communicate with other parallel worker instances

**ALWAYS:**
- Derive `relationship_notes` from snapshot context + codebase analysis, NOT from prior result files
- Write result to `$3/{phase_id}_result.json` (keep `_result.json` suffix — downstream consumers glob `*_result.json`)
- Emit `elab_result_path` output token
- Include all `PhaseElaborated` fields in the result

## Workflow

### Step 1: Parse arguments and read snapshot

Read the plan snapshot at `$1`. It is a `PlanDocument` with a `phases` list of `PhaseShort` objects:
```json
{
  "schema_version": 1,
  "task": "...",
  "source_dir": "...",
  "phases": [
    {"id": "P1", "name": "...", "goal": "...", "scope": [...], "ordering": 1},
    {"id": "P2", "name": "...", "goal": "...", "scope": [...], "ordering": 2},
    ...
  ]
}
```

Find the entry in `phases` where `id == "$2"` (the target phase). Note its `ordering` to
understand which phases come before and after it.

### Step 2: Launch parallel codebase exploration subagents

Spawn up to 5 simultaneous Explore subagents against the codebase in `source_dir`:

1. **Affected files** — Which files/modules fall within this phase's `scope`? Current state, imports, deviations from conventions.
2. **Dependency analysis** — What imports and consumes the affected modules? Full import graph.
3. **Test coverage** — Which tests cover the affected scope? Gaps in coverage?
4. **Pattern discovery** — What conventions and reusable utilities exist in this scope?
5. **Cross-phase boundaries** — Based on snapshot context (other phases' names/goals/scopes), where do likely dependencies or handoff points exist?

### Step 3: Write phase result

Write to `$3/{target_phase_id}_result.json` matching `PhaseElaborated`:
```json
{
  "id": "P3",
  "name": "...",
  "goal": "...",
  "scope": [...],
  "ordering": 3,
  "technical_approach": "...",
  "relationship_notes": "Depends on P1 (...name...) for ...; P5 (...name...) will consume ...",
  "assignments_preview": ["Assignment title 1", "Assignment title 2", ...]
}
```

For `relationship_notes`: use other phases' `name`, `goal`, and `scope` from the snapshot
(not their result files) combined with codebase evidence to identify real dependencies.

Do NOT write `phase_number` or `name_slug` — the backend derives these at load time from
`ordering` and `name` respectively.

### Step 4: Emit output token

```
elab_result_path = <absolute path to $3/{id}_result.json>
```
