---
name: planner-generate-phases
categories: [planner]
description: Generate high-level phases from project analysis (Pass 1 entry point)
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
phase definitions in a single session. The number of phases is determined entirely by the
task — each phase is an independently verifiable checkpoint. Write all phase results and
a fully-done `phase_manifest.json` in one shot.

## When to Use

- Invoked by the planner recipe as the Pass 1 phase-generation step
- After `planner-analyze` (and optionally `planner-extract-domain`) have completed

## Arguments

- **$1** — Absolute path to `analysis.json` produced by `planner-analyze`
- **$2** — (optional) Absolute path to `domain_knowledge.md` produced by `planner-extract-domain`
- **$3** — Absolute path to a file containing the task description

## Critical Constraints

**NEVER:**
- Invent phases that do not serve the task description — every phase must map to work requested by the user
- Anchor phase count to a predetermined number — derive it from the task's natural verification boundaries
- Write output outside `$(dirname $1)/phases/`
- Use freeform text instead of the required JSON schema
- Read files outside `$(dirname $1)` or the project's git-tracked source tree
- Explore parent directories of `$(dirname $1)` (e.g., `ls $(dirname $1)/..`)
- Read `{{AUTOSKILLIT_TEMP}}` artifacts from other planner runs or pipeline steps
- If `$3` is empty or the file does not exist, STOP immediately and report failure. Do not attempt to infer the task from the codebase, GitHub issues, or any other source.

**ALWAYS:**
- Write `$(dirname $1)/phases/{phase_id}_result.json` for every phase
- Write `$(dirname $1)/phases/phase_manifest.json` with every item status=`done`
- Use sequential `ordering` values starting at 1
- Emit `phase_manifest_path`, `phase_count`, and `phase_ids` output tokens

## Workflow

### Step 0: Read task description

Read the task description from the file at `$3`. This is the user's statement of what
they want planned. Every generated phase MUST serve this task.
Do not generate phases for work not described in the task. If the task asks for specific
deliverables (e.g., "split research.yaml into 4 sub-recipes"), the phases should decompose
that work — not decompose the codebase into architectural layers.

### Step 1: Read inputs

Read `analysis.json` from argument $1. If $2 is provided and the file exists, read
`domain_knowledge.md` from $2. Use the domain vocabulary and patterns to inform phase naming
and scope.

### Step 2: Decompose into phases

Decompose the implementation work into phases. A phase is an **independently verifiable
checkpoint** — a coherent set of features whose functionality can be fully tested once
implemented. Draw phase boundaries wherever you can stop and validate that everything
up to that point works.

Phases should be:
- Coherent (each phase has a single, clear goal)
- Ordered by dependency (foundational work first)
- Non-overlapping in scope
- Named to reflect the task's work units, grounded in the codebase's architecture

The number of phases is determined by the task:
- A single-component change may warrant 1 phase
- A multi-component feature may warrant one phase per independently testable component
- There is no minimum or maximum — let the verification boundaries dictate the count

Do NOT provide yourself with concrete domain examples of phase decomposition. Concrete
examples anchor the decomposition pattern regardless of the actual task (pink elephant
effect). Derive phase names and boundaries from the task description and codebase analysis.

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
  "name": "<descriptive phase name derived from task>",
  "goal": "<one-sentence statement of what this phase achieves>",
  "scope": ["<component-a>", "<component-b>"],
  "ordering": 1,
  "relationship_notes": "Foundation phase — no prior dependencies",
  "assignments_preview": ["<assignment-1>", "<assignment-2>", "<assignment-3>"]
}
```

The backend derives two additional fields at load time — do not write them:
- `phase_number` (integer): derived from `ordering`
- `name_slug` (string): derived by slugifying `name` (e.g., "Component Setup" → "component-setup")

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
      "name": "<phase name>",
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
