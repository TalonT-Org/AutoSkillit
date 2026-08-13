---
name: planner-elaborate-wps
uses_capabilities: []
categories:
- planner
description: 'Elaborate all work packages for a target phase via parallel L0 subagents. One invocation per phase; spawns one
  L0 per WP concurrently. (Pass 3 loop body)

  '
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: wp-elaborator
    purpose: elaborate one work package and return a bounded JSON result
  child_spawns:
  - role: wp-elaborator
    for_each: pending_wp_ids
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: wp-elaborator
    model_class: sonnet
---

# planner-elaborate-wps

Pass 3 loop body. Receives one phase context file (written by `expand_wps`), spawns one L0
subagent per WP in parallel using the backend-adapted child delegation, collects results,
and writes per-WP result files plus a phase sentinel file. The L1 (this skill)
is the sole writer for this phase's WPs — no concurrent write races.

## When to Use

- Invoked by the planner recipe once per phase via parallel dispatch (`dispatch_items`)
- One invocation per phase; handles all WPs within the phase in a single session

## Arguments

- **$1** — Absolute path to the phase context file (written by `expand_wps`) (contains `id=<phase_id>`, `metadata.wp_count`, `metadata.wp_ids`, `metadata.wp_names`, `metadata.wp_scopes`, `metadata.wp_estimated_files`, `prior_results`)
- **$2** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`)

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Allow L0 subagents to write files directly — L0s return JSON only
- Let an L0 failure abort the phase — always write a stub and continue
- Write output outside `$2/work_packages/`
- Spawn L0s sequentially — always in parallel
- Spawn more than 6 L0s in one batch — if WP count exceeds 6, use sequential batches of 6
- Read `{{AUTOSKILLIT_TEMP}}` artifacts outside your designated input files and output directory
- Explore parent directories of your input paths (e.g., `ls $(dirname $1)/..`)
- Read result files from other phases
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially

- Write, Edit, or use file-modifying Bash commands (sed -i, echo >, tee) on any file outside the planner output directory ($AUTOSKILLIT_ALLOWED_WRITE_PREFIX). Source code files must NEVER be modified.

**ALWAYS:**
- Spawn all L0 subagents in parallel using the backend-adapted child delegation (NOT run_skill — skill session guard blocks it)
- Write the phase sentinel file before emitting the output token
- Emit: `phase_wps_result_dir = <absolute path to work_packages/ directory>`
- Write a stub result for any L0 that fails or returns invalid JSON
- Start all independent child delegations before awaiting any result to maximize concurrency

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

Read the `task_file_path` field from the context file at $1, then read the task description
from disk at that path. Every WP elaboration — its `deliverables`, `acceptance_criteria`,
and `estimated_files` — must serve the stated task. Do not read the full task text into the
L1 context or embed it in the L0 prompt — pass the path reference only and instruct L0s
to read the file from disk for scope creep verification.

### Step 3: Build per-WP variable-data packets

For each WP in the phase, build a variable-data packet containing ONLY the per-WP context.
The agent definition (`autoskillit:wp-elaborator`) supplies all behavioral instructions,
output format, and constraints — do NOT re-add any static instructions to these packets.

Each packet contains:
- WP ID, name, scope, estimated_files (from manifest metadata)
- Assignment context: the parent assignment's goal, technical_approach
- All sibling WPs for this phase in short-form (id, name, scope only — for dependency detection)
- Phase goal and scope
- Task file path (absolute) — the agent reads this for scope-creep verification

### Step 4: Spawn L0 subagents in PARALLEL (SINGLE MESSAGE)

Before spawning any L0, filter the WP list to skip any WP whose `$2/work_packages/{wp_id}_result.json`
already exists on disk. This idempotency guard prevents redundant L0 re-spawns on retry after
partial completion (e.g., when an L1 session is resumed after context recovery).

Set `pending_wp_ids` to the WP IDs remaining after that filter. This exact collection,
not unfiltered `metadata.wp_ids`, controls batching, prompts, child-ID tracking, result
association, and joins.

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Use the native child delegation with `subagent_type: "autoskillit:wp-elaborator"` to spawn one
agent per item in `pending_wp_ids`. If its count exceeds 6, spawn sequential batches of
at most 6 — await
each batch before starting the next.

Each agent receives its variable-data packet (from Step 3) as the `prompt` parameter.
The agent definition automatically provides:
- Role and tool constraints (You are a READ-ONLY analysis agent. Do NOT use Write, Edit, or Bash to modify any files.)
- Output JSON schema with all required fields
- Deliverable bounds (1–5)
- WP ID format contract
- Task file scope-creep verification instructions

Do NOT embed any of the above in the prompt — the agent definition handles it.
Duplicating instructions wastes L1 context budget.

**WP ID Format Reference:** WP IDs produced by the agents must match `P{N}-A{N}-WP{N}`
where `{N}` is a positive integer (numeric only). Non-matching IDs are rejected at validation.

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

After writing all WP files, the manifest is rebuilt downstream by `finalize_wp_manifest`
which scans disk for all `{wp_id}_result.json` files and writes `wp_index.json` atomically.
Do not append to `wp_index.json` directly — concurrent L1 sessions would race on the shared file.

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

## Context Limit Behavior

This skill writes result files to the output directory during execution.
If context is exhausted mid-execution:

1. Commit any pending file writes to disk before exiting.
2. The caller's `on_context_limit` routing handles recovery — do not attempt partial structured output.
