---
name: planner-elaborate-assignment
categories: [planner]
description: Elaborate a single assignment with cross-phase dependency and overlap analysis (Pass 2 loop body)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-elaborate-assignment] Elaborating assignment...'"
          once: true
---

# planner-elaborate-assignment

Pass 2 loop body. Elaborates a single assignment with full awareness of all prior
assignments across all phases. Uses sub-agents to scan for dependencies and overlaps.
Produces the `proposed_work_packages` array — the critical bridge to Pass 3.

## When to Use

- Invoked by the planner recipe's Pass 2 loop when `check_remaining` returns `has_remaining: "true"`
- One invocation per assignment, sequentially in phase/assignment order

## Arguments

- **$1** — Absolute path to the context file written by `check_remaining`

## Critical Constraints

**NEVER:**
- Omit `proposed_work_packages` from the result — it is mandatory
- Use freeform strings in WP entries — every entry must have `id_suffix`, `name`, `scope`, `estimated_files`
- Write output outside `{{AUTOSKILLIT_TEMP}}/planner/assignments/`
- Make `estimated_files` exhaustive — it is guidance, not a contract

**ALWAYS:**
- Spawn dependency and overlap sub-agents in parallel
- Include at least 1 work package per assignment
- Ensure WP `id_suffix` values are unique within the assignment (WP1, WP2, ...)
- Emit `assignment_result_path` output token

## Workflow

### Step 1: Read context file

Read the context file at $1:
```json
{
  "id": "P1-A2",
  "name": "Session Management",
  "metadata": {
    "phase_id": "P1",
    "phase_name": "Database Layer",
    "goal": "Implement user session persistence"
  },
  "prior_results": [
    "<path>/P1-A1_result.json",
    "<path>/P1-A0_result.json"
  ],
  "wp_index_path": "<path>/wp_index.json"
}
```

### Step 2: Spawn parallel sub-agents

Launch both sub-agents concurrently with `model: "sonnet"`:

1. **Dependency Scanner** — Read prior results and identify which prior assignments this
   one depends on. Report: assignment IDs depended upon, specific artifacts/interfaces
   needed (e.g., "requires P1-A1's database schema").

2. **Overlap Scanner** — Read prior results and identify scope overlaps to avoid. Report:
   any prior assignment that touched similar files or components, flagging anything that
   should NOT be duplicated here.

### Step 3: Decompose into work packages

Based on the assignment goal, phase context, and scanner findings, decompose the assignment
into 1–5 work packages. Each WP should be implementable in a single focused session.

For each WP, define:
- `id_suffix`: Sequential suffix "WP1", "WP2", ... (unique within this assignment)
- `name`: Short action-oriented name (e.g., "Create session table migration")
- `scope`: One-sentence description of what this WP covers
- `estimated_files`: Array of file paths likely to be created or modified

### Step 4: Write assignment result

Write to `{{AUTOSKILLIT_TEMP}}/planner/assignments/{id}_result.json` (relative to the current working directory):
```json
{
  "id": "P1-A2",
  "name": "Session Management",
  "phase_id": "P1",
  "goal": "Implement user session persistence",
  "technical_approach": "SQLite-backed session table with CRUD repository layer",
  "proposed_work_packages": [
    {
      "id_suffix": "WP1",
      "name": "Create session table migration",
      "scope": "Database migration and model for sessions",
      "estimated_files": [
        "src/db/migrations/002_sessions.py",
        "src/db/models/session.py"
      ]
    },
    {
      "id_suffix": "WP2",
      "name": "Session CRUD operations",
      "scope": "Repository methods for session lifecycle",
      "estimated_files": ["src/db/repos/session_repo.py"]
    }
  ]
}
```

The backend derives two additional fields at load time — do not write them:
- `phase_number` (integer): derived by parsing the phase component of `id` (e.g., "P1-A2" → 1)
- `assignment_number` (integer): derived by parsing the assignment component of `id` (e.g., "P1-A2" → 2)

### Step 5: Emit output token

```
assignment_result_path = <absolute path to {id}_result.json>
```
