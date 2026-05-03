---
name: planner-elaborate-assignments
categories: [planner]
description: >
  Elaborate all assignments for a target phase via parallel L0 subagents.
  One invocation per phase; spawns one L0 per assignment concurrently. (Pass 2 loop body)
---

# planner-elaborate-assignments

Pass 2 loop body. Receives one phase context file (written by `expand_assignments`), spawns one L0
subagent per assignment in parallel using the native Agent/Task tool, collects results,
and writes per-assignment result files plus a phase sentinel file. The L1 (this skill)
is the sole writer for this phase's assignments — no concurrent write races.

## When to Use

- Invoked by the planner recipe when the recipe dispatches parallel elaboration via `dispatch_items`
- One invocation per phase; handles all assignments within the phase in a single session

## Arguments

- **$1** — Absolute path to the phase context file (written by `expand_assignments`) (contains `id=<phase_id>`, `metadata.assignment_count`, `metadata.assignment_ids`, `metadata.assignment_names`, `prior_results`)
- **$2** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`)

## Critical Constraints

**NEVER:**
- Allow L0 subagents to write files directly — L0s return JSON only
- Let an L0 failure abort the phase — always write a stub and continue
- Write output outside `$2/assignments/`
- Spawn L0s sequentially — always in parallel
- Read `{{AUTOSKILLIT_TEMP}}` artifacts outside your designated input files and output directory
- Explore parent directories of your input paths (e.g., `ls $(dirname $1)/..`)
- Read result files from other phases
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Spawn all L0 subagents in parallel using the native Agent/Task tool (NOT run_skill — leaf guard blocks it)
- Write the phase sentinel file before emitting the output token
- Emit `phase_assignments_result_dir`
- Write a stub result for any L0 that fails or returns invalid JSON

## Workflow

### Step 1: Parse context file

Read the context file at `$1`. Extract:
- `id` — the phase ID (e.g., `"P1"`)
- `metadata.assignment_ids` — list of assignment IDs for this phase
- `metadata.assignment_names` — parallel list of assignment names
- `prior_results` — list of paths to result files from completed prior items

### Step 2: Load phase context

Read `$2/phases/{id}_result.json` (the elaborated phase result). Extract:
- `goal` — phase-level goal
- `scope` — phase-level scope list
- `technical_approach` — overall technical approach for the phase
- `assignments` — array of assignment objects with `name`, `goal`, `metadata`

Read the `task` field from the context file at $1. Every assignment elaboration — its `goal`,
`scope`, `deliverables`, and `work_packages_preview` — must serve the stated task. Do not
elaborate into work not requested by the task.

### Step 3: Build per-L0 context packets

For each assignment in the phase, build a self-contained context packet:
- Assignment ID, name, goal (from the phase result's assignments array)
- All other phase assignments in short-form (id, name, goal only — for overlap detection)
- Prior result file paths (from `prior_results`) for cross-phase dependency detection
- The phase's `technical_approach` and `scope`
- The planner directory path `$2` for reading prior results if needed

### Step 4: Spawn L0 subagents in PARALLEL

Use the native Agent/Task tool to spawn one L0 per assignment simultaneously.
All L0s must be launched in a single batch — do NOT wait for one before starting the next.

Each L0 receives a self-contained prompt that:
1. Identifies the assignment (ID, name, goal)
2. Provides short-form context for all other phase assignments
3. Lists prior result file paths for cross-phase overlap analysis
4. Instructs the L0 to use Grep/Glob/Read for codebase analysis (no sub-subagents)
5. Instructs the L0 to decompose into 1–5 work packages
6. Instructs the L0 to return results as JSON between triple-backtick json fences

Each L0 MUST:
- Use Grep/Glob/Read for codebase analysis (no sub-subagent spawning — they are leaf sessions)
- Scan for dependencies by comparing scope against other phase assignments (short-form provided in prompt)
- Read prior result files (if paths provided) to detect cross-phase overlaps
- Decompose into 1–5 work packages with: `id_suffix` (WP1, WP2, ...), `name`, `scope`, `estimated_files`
- Return structured JSON between ` ```json ` and ` ``` ` delimiters
- Include `dependency_notes` (string) and `overlap_notes` (string) in the JSON

Expected L0 return schema:
```json
{
  "id": "P1-A2",
  "phase_id": "P1",
  "name": "<assignment name>",
  "goal": "<one-sentence goal>",
  "technical_approach": "<technical approach description>",
  "dependency_notes": "Depends on P1-A1 for <dependency description>",
  "overlap_notes": "No overlap detected with other assignments",
  "proposed_work_packages": [
    {
      "id_suffix": "WP1",
      "name": "<work package name>",
      "scope": "<scope description>",
      "estimated_files": [
        "src/<path>/<file_a>.py",
        "src/<path>/<file_b>.py"
      ]
    }
  ]
}
```

### Step 5: Collect and validate L0 responses

For each L0 response:
1. Extract JSON from between ` ```json ` and ` ``` ` delimiters
2. Validate required fields: `id`, `phase_id`, `name`, `goal`, `technical_approach`, `proposed_work_packages`
3. On validation failure OR no valid JSON found: emit `WARNING: L0 elaboration failed for {assignment_id}` and mark for stub creation

### Step 6: Write per-assignment files

For each successful L0 result, write `$2/assignments/{assignment_id}_result.json` with the full result JSON.

For each failed L0, write `$2/assignments/{assignment_id}_result.json` with:
```json
{
  "id": "...",
  "phase_id": "...",
  "name": "...",
  "goal": "...",
  "technical_approach": "",
  "proposed_work_packages": [],
  "elaboration_failed": true
}
```

After writing all assignment files, update `$2/wp_index.json` by appending compact entries
for all **successful** results only (skip stubs). Read the current index, append, and write back
atomically. L1 is the sole writer for this phase's assignments — no concurrent writes.

Finally, write the phase sentinel file to `$2/assignments/{phase_id}_result.json`:
```json
{"id": "<phase_id>", "status": "complete", "assignment_count": N, "failed_count": M}
```

> The sentinel path MUST be `$2/assignments/{phase_id}_result.json`. The manifest's
> `result_dir` points to `$2/assignments/`, and this path is used to detect phase
> completion. Verify the path before writing.

### Step 7: Emit output token

```
phase_assignments_result_dir = <absolute path to $2/assignments>
```
