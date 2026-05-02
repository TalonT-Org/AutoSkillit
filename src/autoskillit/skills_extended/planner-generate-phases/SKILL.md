---
name: planner-generate-phases
categories: [planner]
description: Generate 3-6 high-level phases from project analysis (Pass 1 entry point)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-generate-phases] Generating phases...'"
          once: true
---

# planner-generate-phases

Pass 1 entry point. Read the analysis file (and optionally domain knowledge) and produce
3–6 phase definitions in a single session. Write all phase results and a fully-done
`phase_manifest.json` in one shot.

## When to Use

- Invoked by the planner recipe as the Pass 1 phase-generation step
- After `planner-analyze` (and optionally `planner-extract-domain`) have completed

## Arguments

- **$1** — Absolute path to `analysis.json` produced by `planner-analyze`
- **$2** — (optional) Absolute path to `domain_knowledge.md` produced by `planner-extract-domain`

### Environment Variables

- **PLANNER_TASK** (required unless PLANNER_TASK_FILE is set) — The user's task description. Every phase MUST serve this task.
- **PLANNER_TASK_FILE** (env-var, optional) — Absolute path to a file containing the task description. When set, read the file content and use it as the task. Takes precedence over `PLANNER_TASK`.

## Critical Constraints

**NEVER:**
- Produce fewer than 3 or more than 6 phases
- Write output outside `$(dirname $1)/phases/`
- Use freeform text instead of the required JSON schema
- Read files outside `$(dirname $1)` or the project's git-tracked source tree
- Explore parent directories of `$(dirname $1)` (e.g., `ls $(dirname $1)/..`)
- Read `{{AUTOSKILLIT_TEMP}}` artifacts from other planner runs or pipeline steps

**ALWAYS:**
- Write `$(dirname $1)/phases/{phase_id}_result.json` for every phase
- Write `$(dirname $1)/phases/phase_manifest.json` with every item status=`done`
- Use sequential `ordering` values starting at 1
- Emit `phase_manifest_path`, `phase_count`, and `phase_ids` output tokens

## Workflow

### Step 0: Read task description

If `PLANNER_TASK_FILE` is set, read the file at that path to obtain the task description. Otherwise read `PLANNER_TASK` from the environment. This is the user's
statement of what they want planned. Every generated phase MUST serve this task.
Do not generate phases for work not described in the task. If the task asks for specific
deliverables (e.g., "split research.yaml into 4 sub-recipes"), the phases should decompose
that work — not decompose the codebase into architectural layers.

### Step 1: Read inputs

Read `analysis.json` from argument $1. If $2 is provided and the file exists, read
`domain_knowledge.md` from $2. Use the domain vocabulary and patterns to inform phase naming
and scope.

### Step 2: Decompose into phases

Identify 3–6 high-level phases that partition the implementation work. Phases should be:
- Coherent (each phase has a single, clear goal)
- Ordered by dependency (foundational work first)
- Non-overlapping in scope
- Named to reflect the task's work units, grounded in the codebase's architecture (e.g., if the task is "add user authentication", phases might be "Auth Data Model", "Auth API Endpoints", "Auth UI Integration")

For each phase, generate:
- `id`: Sequential `P{N}` identifier (P1, P2, ...)
- `name`: Short, descriptive phase name
- `goal`: One-sentence statement of what the phase achieves
- `scope`: Array of domain areas or component names covered
- `ordering`: Integer sequence position (1-based)
- `relationship_notes`: Description of dependencies on prior phases ("Foundation phase — no prior dependencies" for P1)
- `assignments_preview`: Array of 2–5 short strings naming likely assignments within this phase

### Step 3: Write phase results

For each phase, write to `$(dirname $1)/phases/{phase_id}_result.json`:

```json
{
  "id": "P1",
  "name": "Database Layer",
  "goal": "Establish data persistence and schema foundations",
  "scope": ["models", "migrations", "repositories"],
  "ordering": 1,
  "relationship_notes": "Foundation phase — no prior dependencies",
  "assignments_preview": ["Schema design", "Migration framework", "Repository pattern"]
}
```

The backend derives two additional fields at load time — do not write them:
- `phase_number` (integer): derived from `ordering`
- `name_slug` (string): derived by slugifying `name` (e.g., "Database Layer" → "database-layer")

### Step 4: Write phase manifest

Write `$(dirname $1)/phases/phase_manifest.json`. Set every item's status to
`done` (Pass 1 is coarse-grained enough to resolve in one shot; the elaborate loop exists
only as a fallback). Set `result_path` to the absolute path of the corresponding result file.

Manifest structure:
```json
{
  "pass_name": "phases",
  "created_at": "<ISO8601 timestamp>",
  "items": [
    {
      "id": "P1",
      "name": "Database Layer",
      "status": "done",
      "result_path": "<absolute_path>/phases/P1_result.json",
      "metadata": {"ordering": 1}
    }
  ]
}
```

### Step 5: Emit output tokens

```
phase_manifest_path = <absolute path to phase_manifest.json>
phase_count = <N>
phase_ids = <comma-separated list of phase IDs, e.g. P1,P2,P3>
```
