---
name: planner-refine
categories: [planner]
description: Targeted fix of validate_plan findings — re-elaboration, duplicate resolution, dependency corrections
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-refine] Refining plan...'"
          once: true
---

# planner-refine

Targeted repair of `validate_plan` findings. Loads `validation.json` and repairs each
finding type: re-elaborates failed WPs, resolves duplicate deliverable ownership, and
fixes dependency reference errors. Sizing violations are escalated as CRITICAL. Writes
corrected artifacts back to the output directory so `validate_plan` can re-run.

The recipe runs this skill with `retries: 2` — up to 3 total attempts (1 initial + 2
retries) before escalation.

## When to Use

- Invoked by the planner recipe when `validate_plan` returns `verdict: fail`
- One invocation per retry cycle

## Arguments

- **$1** — Absolute path to `validation.json`
- **$2** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`)

## Critical Constraints

**NEVER:**
- Attempt to auto-fix missing assignments or missing WPs — these require human review
- Remove a deliverable without reassigning it to another WP
- Introduce new WP IDs — the skill never creates WPs; it repairs or escalates existing ones

**ALWAYS:**
- Load `validation.json` before reading any artifact
- Fix all addressable finding types in a single pass
- Update `wp_manifest.json` and `wp_index.json` whenever WP structure changes
- Escalate sizing violations, missing structural elements, malformed WP IDs, and DAG cycles as CRITICAL (write to stdout; do not count toward issues_fixed)
- Emit both `refinement_complete` and `issues_fixed` output tokens

## Workflow

### Step 1: Load validation.json

Read `$1`. Extract the `findings` array (contains only error-severity findings as structured
dicts). Extract the `message` field from each finding for classification. Group by type:

- **failed_wps**: Findings matching `WP .* has status 'failed'`
- **sizing_violations**: Findings matching `WP .* has \d+ deliverables`
- **duplicate_deliverables**: Findings matching `Deliverable '.*' claimed by multiple WPs`
- **dep_references**: Findings matching `WP .* depends on unknown WP`
- **missing**: Findings matching `Phase .* has no assignments` or `Assignment .* has no work packages`
- **malformed_id**: Findings matching `WP .* has malformed id`
- **dag_cycle**: Findings matching `Cycle detected among WPs`
- **files_touched_overlap** (informational): Findings matching `File '.*' touched by multiple WPs` — these appear in the `warnings` array, not `findings`. No action needed; skip if encountered in `findings`.

### Step 2: Load required artifacts

- Always load: `{$2}/work_packages/wp_manifest.json`, `{$2}/work_packages/wp_index.json`
- Load only the `{id}_result.json` files for WPs mentioned in the findings

### Step 3: Fix each finding type

**Failed WPs** — re-elaborate:
- For each failed WP ID, read its entry from `wp_manifest.json` (provides `name`, `scope`,
  `estimated_files`)
- Spawn a sub-agent with `model: "sonnet"` per failed WP. Provide: WP name, scope,
  estimated_files, and the relevant portion of `wp_index.json` for context
- Sub-agent writes a corrected `{$2}/work_packages/{id}_result.json`
- Sub-agent appends corrected compact entry to `wp_index.json`
- Update the WP status in `wp_manifest.json` from `failed` to `done`

**Sizing violations** — escalate:
- Findings matching `WP .* has \d+ deliverables` indicate WPs outside the 1–5 deliverable
  sizing bound. Cannot be auto-corrected — the implementation recipe handles re-splitting
  downstream.
```
CRITICAL: Cannot auto-fix sizing violation:
- {finding text}
Manual review of WP deliverable allocation required.
```
Write this to stdout. Do NOT attempt WP splitting or merging.

**Duplicate deliverables** — resolve ownership:
- For each duplicated file path, assign ownership to the WP whose scope most directly
  implements that file (strongest semantic claim)
- Remove the duplicate from the lower-priority WP's `deliverables` (keep in `files_touched`)
- Write updated `_result.json` for the affected WPs

**Dependency reference errors** — fix broken dep IDs:
- For each `WP X depends on unknown WP Y` finding:
  - Search `wp_index.json` for a WP with a similar name or scope to the missing `Y`
    (it may have been renamed or split)
  - If a valid replacement is found, update `depends_on` in `{$2}/work_packages/{X}_result.json`
  - If no valid replacement exists, remove the broken reference from `depends_on`
- If `dep_graph.json` exists, update it to reflect corrected dependency IDs

**Missing assignments/WPs** — escalate:
```
CRITICAL: Cannot auto-fix missing structural elements:
- {finding text}
Manual intervention required before validate_plan can pass.
```
Write this to stdout. Do NOT attempt structural creation.

**Malformed WP IDs** — escalate:
- Findings matching `WP .* has malformed id (expected PX-AY-WPZ)` indicate a corrupted
  `_result.json` or `wp_manifest.json`. Cannot be auto-corrected without understanding the
  intended ID.
```
CRITICAL: Cannot auto-fix malformed WP ID:
- {finding text}
Manual inspection of wp_manifest.json required.
```
Write this to stdout. Do NOT attempt ID renaming.

**DAG cycles** — escalate:
- Findings matching `Cycle detected among WPs: ...` indicate a circular dependency in the
  WP graph. Resolving cycles requires semantic understanding of the plan structure.
```
CRITICAL: Cannot auto-fix DAG cycle:
- {finding text}
Manual restructuring of depends_on relationships required.
```
Write this to stdout. Do NOT attempt cycle-breaking.

### Step 4: Write corrected artifacts

Write all modified files back atomically (read current → apply change → write). Modified
files may include: `_result.json` files, `wp_manifest.json`, `wp_index.json`,
`dep_graph.json`.

> **Note:** Combined documents (`combined_*.json`, `refined_*.json`) are intermediate
> orchestration artifacts and are NOT updated by this skill. Downstream consumers
> (`validate_plan`, `compile_plan`) read from individual `*_result.json` files directly,
> so stale combined documents do not affect pipeline correctness.

### Step 5: Emit output tokens

```
refinement_complete = true
issues_fixed = <N>
```

`N` = count of findings addressed from the `findings` array (failed_wps +
duplicate_deliverables + dep_references). Sizing-violation, missing-element, malformed-ID,
and DAG-cycle findings are excluded from the count (they are escalated as critical, not
fixed). Files-touched overlap findings are in the `warnings` array and are not actionable.
