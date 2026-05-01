---
name: planner-elaborate-wps
categories: [planner]
description: >
  Elaborate all work packages for a target phase via parallel L0 subagents.
  One invocation per phase; spawns one L0 per WP concurrently. (Pass 3 loop body)
---

# planner-elaborate-wps

Pass 3 loop body. Receives one phase context file (written by `expand_wps`), spawns one L0
subagent per WP in parallel using the native Agent/Task tool, collects results,
and writes per-WP result files plus a phase sentinel file. The L1 (this skill)
is the sole writer for this phase's WPs — no concurrent write races.

## When to Use

- Invoked by the planner recipe once per phase via sequential dispatch (`dispatch_items`)
- One invocation per phase; handles all WPs within the phase in a single session

## Arguments

- **$1** — Absolute path to the phase context file (written by `expand_wps`) (contains `id=<phase_id>`, `metadata.wp_count`, `metadata.wp_ids`, `metadata.wp_names`, `metadata.wp_scopes`, `metadata.wp_estimated_files`, `prior_results`)
- **$2** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`)

## Critical Constraints

**NEVER:**
- Allow L0 subagents to write files directly — L0s return JSON only
- Let an L0 failure abort the phase — always write a stub and continue
- Write output outside `$2/work_packages/`
- Spawn L0s sequentially — always in parallel
- Spawn more than 6 L0s in one batch — if WP count exceeds 6, use sequential batches of 6
- Read `{{AUTOSKILLIT_TEMP}}` artifacts outside your designated input files and output directory
- Explore parent directories of your input paths (e.g., `ls $(dirname $1)/..`)
- Read result files from other phases
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Spawn all L0 subagents in parallel using the native Agent/Task tool (NOT run_skill — leaf guard blocks it)
- Write the phase sentinel file before emitting the output token
- Emit `phase_wps_result_dir`
- Write a stub result for any L0 that fails or returns invalid JSON

## Workflow

### Step 1: Parse context file

Read the context file at `$1`. Extract:
- `id` — the phase ID (e.g., `"P1"`)
- `metadata.wp_ids` — list of WP IDs for this phase (e.g., `["P1-A1-WP1", "P1-A1-WP2"]`)
- `metadata.wp_names` — parallel list of WP names
- `metadata.wp_scopes` — parallel list of WP scopes
- `metadata.wp_estimated_files` — parallel list of estimated file lists
- `prior_results` — list of paths to result files from completed prior items

### Step 2: Load phase and assignment context

Read `$2/phases/{id}_result.json` (the elaborated phase result). Extract:
- `goal` — phase-level goal
- `scope` — phase-level scope list
- `technical_approach` — overall technical approach for the phase

Read all `$2/assignments/P{N}-A*_result.json` matching this phase to get assignment-level context:
- `goal` — assignment goal
- `technical_approach` — assignment technical approach
- `proposed_work_packages` — the WP decomposition from the assignment pass

Read the `task` field from the context file at $1. Every WP elaboration — its `deliverables`,
`acceptance_criteria`, and `estimated_files` — must serve the stated task. Do not create WPs
for work not requested by the task.

### Step 3: Build per-L0 context packets

For each WP in the phase, build a self-contained context packet:
- WP ID, name, scope, estimated_files (from manifest metadata)
- Assignment context: the parent assignment's goal, technical_approach
- All sibling WPs for this phase in short-form (id, name, scope only — for dependency detection)
- Phase goal and scope
- Instruct L0 to use Grep/Glob/Read for codebase analysis (no sub-subagents)
- Instruct L0 to return JSON between triple-backtick json fences

### Step 4: Spawn L0 subagents in PARALLEL

Use the native Agent/Task tool to spawn one L0 per WP simultaneously.
If WP count > 6, spawn in sequential batches of 6 — await each batch before starting the next.

Each L0 receives a self-contained prompt that:
1. Identifies the WP (ID, name, scope, estimated_files)
2. Provides assignment context (goal, technical_approach)
3. Lists all sibling WPs in short-form for dependency detection
4. Provides phase goal and scope
5. Instructs the L0 to use Grep/Glob/Read for codebase analysis (no sub-subagents)
6. Instructs the L0 to return results as JSON between triple-backtick json fences

Each L0 MUST:
- Use Grep/Glob/Read for codebase analysis (no sub-subagent spawning — they are leaf sessions)
- Elaborate the WP with all mandatory fields
- Return structured JSON between ` ```json ` and ` ``` ` delimiters

Expected L0 return schema:
```json
{
  "id": "P1-A2-WP1",
  "name": "...",
  "goal": "...",
  "summary": "<=120 chars",
  "technical_steps": ["..."],
  "files_touched": ["..."],
  "apis_defined": ["..."],
  "apis_consumed": ["..."],
  "depends_on": ["..."],
  "deliverables": ["1-5 files"],
  "acceptance_criteria": ["..."]
}
```

### Step 5: Collect and validate L0 responses

For each L0 response:
1. Extract JSON from between ` ```json ` and ` ``` ` delimiters
2. Validate required fields: `id`, `name`, `deliverables` (matching `WP_REQUIRED_KEYS`)
3. On validation failure OR no valid JSON found: emit `WARNING: L0 elaboration failed for {wp_id}` and mark for stub creation. CRITICAL: partial failure must not abort the phase.

### Step 6: Write per-WP files

For each successful L0 result, write `$2/work_packages/{wp_id}_result.json` with the full result JSON.

For each failed L0, write `$2/work_packages/{wp_id}_result.json` with:
```json
{
  "id": "...",
  "name": "...",
  "deliverables": [],
  "elaboration_failed": true,
  "summary": "",
  "goal": "",
  "technical_steps": [],
  "files_touched": [],
  "apis_defined": [],
  "apis_consumed": [],
  "depends_on": [],
  "acceptance_criteria": []
}
```

After writing all WP files, update `$2/wp_index.json` by appending compact entries
for all **successful** results only (skip stubs). Read the current index, append, and write back
atomically. L1 is the sole writer for this phase's WPs — no concurrent writes.

Finally, write the phase sentinel file to `$2/work_packages/wp_sentinels/{phase_id}_result.json`:
```json
{"id": "<phase_id>", "status": "complete", "wp_count": N, "failed_count": M}
```

> The sentinel path MUST be `$2/work_packages/wp_sentinels/{phase_id}_result.json`. The manifest's
> `result_dir` points to `$2/work_packages/wp_sentinels/`, and this path is used to detect
> phase completion. Verify the path before writing.

### Step 7: Emit output token

```
phase_wps_result_dir = <absolute path to $2/work_packages>
```
