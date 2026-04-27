---
name: planner-elaborate-phase
categories: [planner]
description: Elaborate a single phase with cross-phase relationship notes (Pass 1 fallback loop body)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-elaborate-phase] Elaborating phase...'"
          once: true
---

# planner-elaborate-phase

Pass 1 loop body. Used when `planner-generate-phases` produces >6 phases (rare) or when
the planner recipe requires an explicit per-phase elaboration loop. Reads a context file
describing one phase and all prior phase results, then writes a complete phase result.

## When to Use

- Invoked by the planner recipe's Pass 1 loop when `check_remaining` returns `has_remaining: "true"`
- Only used when phase count exceeds 6 (fallback path)

## Arguments

- **$1** — Absolute path to the context file written by `check_remaining`
- **$2** — Absolute path to the run-scoped planner directory (e.g., `/path/to/.autoskillit/temp/planner/run-YYYYMMDD-HHMMSS`)

## Critical Constraints

**NEVER:**
- Write output outside `$2/phases/`
- Omit `relationship_notes` from the result

**ALWAYS:**
- Read all prior phase results listed in the context file before writing
- Set `relationship_notes` based on actual dependencies identified in prior results
- Write the result to `$2/phases/{id}_result.json` where `{id}` comes from the context file (the context file does NOT contain a `result_path` field)
- Emit `phase_result_path` output token

## Workflow

### Step 1: Read context file

Read the context file at $1. It has this structure:
```json
{
  "id": "P4",
  "name": "Notification Layer",
  "metadata": {"ordering": 4},
  "prior_results": [
    "<path>/P1_result.json",
    "<path>/P2_result.json",
    "<path>/P3_result.json"
  ],
  "wp_index_path": "<path>/wp_index.json"
}
```

### Step 2: Read prior phase results

Read each file listed in `prior_results`. Use the `name`, `goal`, and `scope` fields to
understand what has already been allocated. Identify dependencies: does this phase depend
on any prior phase's deliverables?

### Step 3: Write phase result

Write to `$2/phases/{id}_result.json`:
```json
{
  "id": "P4",
  "name": "Notification Layer",
  "goal": "...",
  "scope": ["..."],
  "ordering": 4,
  "relationship_notes": "Depends on P2 (API Layer) for HTTP client infrastructure",
  "assignments_preview": ["..."]
}
```

The backend derives two additional fields at load time — do not write them:
- `phase_number` (integer): derived from `ordering`
- `name_slug` (string): derived by slugifying `name` (e.g., "Notification Layer" → "notification-layer")

### Step 4: Emit output token

```
phase_result_path = <absolute path to {id}_result.json>
```
