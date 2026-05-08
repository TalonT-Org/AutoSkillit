---
name: planner-consolidate-wps
categories: [planner]
description: Analyze WP complexity per phase and emit consolidation group manifests for trivial WP merging (L1+L0 pattern)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-consolidate-wps] Analyzing WP complexity for consolidation...'"
          once: true
---

# planner-consolidate-wps

L1 session that analyzes WP complexity per phase and proposes consolidation groups
for trivial work packages. Dispatches one L0 subagent per phase in parallel. Each
L0 evaluates per-WP complexity, groups trivial WPs that share the same assignment
and have sequential ordering or a direct dependency chain, and writes a
`{phase_id}_consolidation.json` manifest. The L1 validates each response and writes
the manifest files to `{planner_dir}/work_packages/consolidation/`.

This is an analysis-only step: even if all L0s fail, the downstream
`consolidate_wps_merge` callable performs a no-op passthrough (no manifests = no
merging).

## When to Use

- Launched by the L2 planner recipe after `refine_wps`, before `consolidate_wps_merge`
- Receives `refined_wps.json` (full WP set) and the planner run directory
- Produces per-phase `{phase_id}_consolidation.json` files

## Arguments

- **$1** — Absolute path to `refined_wps.json` (PlanDocument with `work_packages: list[WPElaborated]`)
- **$2** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`)

## Critical Constraints

**NEVER:**
- Merge WPs from different assignments unless they have a direct dependency linking them
- Create a merged group that would clearly exceed a medium-complexity PR (use judgment — no hard threshold)
- Skip a WP from a manifest — every WP must appear in exactly one group (singleton = no-op)
- Use a `merged_id` that is not one of the `source_wp_ids`
- Allow an L0 to write files outside `$2/work_packages/consolidation/`
- Run subagents in the background (`run_in_background: true` is prohibited)
- Spawn more than 6 L0s in a single parallel batch

**ALWAYS:**
- Create `$2/work_packages/consolidation/` before dispatching L0s
- Validate each L0 response before writing its manifest
- Write a manifest for every phase, even if it contains only singleton groups
- Emit: `consolidation_manifest_dir = {planner_dir}/work_packages/consolidation`

## Workflow

### Step 1: Parse inputs

Read `$1` (refined_wps.json). Parse as a PlanDocument. Extract all WP entries from
`work_packages[]`. Fail immediately if the file is malformed:
```
FATAL: failed to parse {path}: {error_detail}
```

Group WPs by `phase_id` (always populated; read directly from the field).
Build a map `phase_id → [WPElaborated, ...]`.

### Step 2: Create output directory

```bash
mkdir -p "$2/work_packages/consolidation"
```

### Step 3: Build L0 context packets

For each phase, assemble a context packet containing:
- `phase_id` — the phase being analyzed
- `wps` — the full list of WP objects in this phase (id, name, goal, technical_steps,
  files_touched, deliverables, acceptance_criteria, depends_on, estimated_files, scope)
- `all_wp_ids` — WP IDs from every phase (for cross-phase dep awareness)
- Merge guidance:
  - **Default: merge.** WPs within the same assignment should be merged unless there is
    a clear reason to keep them separate.
  - **Merge when:** WPs share files, do closely related work, form a natural unit of
    implementation, or would be awkward as separate PRs.
  - **Keep separate when:** WPs represent genuinely independent concerns that a developer
    would naturally implement and review as separate PRs.
  - **Soft upper bound:** Avoid merging into a group that would clearly exceed a
    medium-complexity PR (rough guide: >15 substantive technical steps or >10 distinct
    files). This is guidance, not a hard gate — use judgment about whether the combined
    work forms a coherent unit.

### Step 4: Dispatch parallel L0 subagents

If phase count ≤ 6: spawn all in one parallel batch.
If phase count > 6: spawn sequential batches of 6.

Each L0 receives the phase context packet and must return structured text in this exact format:
```
phase_id = P1
groups = [
  {
    "merged_id": "P1-A1-WP1",
    "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
    "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
    "name": null,
    "goal": null
  },
  {
    "merged_id": "P1-A1-WP3",
    "source_wp_ids": ["P1-A1-WP3"],
    "merge_order": ["P1-A1-WP3"],
    "name": null,
    "goal": null
  }
]
```

L0 grouping rules:
- Assign each WP to exactly one group (solo or merged).
- **Default to merging** WPs within the same assignment. Only keep WPs as singletons when
  they represent genuinely distinct concerns.
- Cross-assignment merges are allowed when WPs share a direct dependency AND touch the
  same files.
- Use judgment about combined complexity. Avoid creating groups that would clearly be
  too large for a single PR, but do not apply hard thresholds on step/file counts.
  Read the actual work described and decide: "would a developer naturally do these
  together?"
- `merged_id` MUST be the lowest-numbered WP ID in the group (primary WP).
- `merge_order` defines the technical_steps concatenation order; typically
  `[primary, ...others_by_id_order]`.
- `name` and `goal` may be null (inherit from primary) or a short override string when
  the merged purpose is clearly distinct from the primary WP's name.

### Step 5: Validate L0 responses

For each L0 response:
- `phase_id` must be present and match the expected phase ID
- `groups` must be a valid JSON array
- Every WP in the phase must appear in exactly one group's `source_wp_ids`
- `merged_id` must be one of the `source_wp_ids`

On `phase_id` mismatch:
```
WARNING: L0 response phase_id mismatch — expected {expected}, got {actual} — skipping
```

On validation failure:
```
WARNING: L0 response for {phase_id} failed validation — skipping
```

On complete L0 failure:
```
CRITICAL: L0 for {phase_id} failed — no manifest written for this phase
```

### Step 6: Write manifests

For each validated L0 response, write the manifest file:
```
$2/work_packages/consolidation/{phase_id}_consolidation.json
```

Manifest format:
```json
{
  "phase_id": "P1",
  "groups": [
    {
      "merged_id": "P1-A1-WP1",
      "source_wp_ids": ["P1-A1-WP1", "P1-A1-WP2"],
      "merge_order": ["P1-A1-WP1", "P1-A1-WP2"],
      "name": null,
      "goal": null
    }
  ]
}
```

### Step 7: Emit output token

```
consolidation_manifest_dir = $2/work_packages/consolidation
```
