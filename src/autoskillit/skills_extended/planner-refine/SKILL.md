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
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Attempt to auto-fix missing assignments or missing WPs — these require human review
- Remove a deliverable without reassigning it to another WP
- Introduce new WP IDs — the skill never creates WPs; it repairs or escalates existing ones

- Write, Edit, or use file-modifying Bash commands (sed -i, echo >, tee) on any file outside the planner output directory ($AUTOSKILLIT_ALLOWED_WRITE_PREFIX). Source code files must NEVER be modified.

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

- **failed_wps**: Findings matching `WP .* has status '(?!done')[^']*'` (any non-done status including `elaboration_failed`)
- **stub_consistency**: Findings matching `WP .* has status 'done' but elaboration_failed in content` — route to the same re-elaboration action as `failed_wps` (re-run `finalize_wp_manifest` to regenerate the manifest from content, then re-elaborate the stub WP)
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

**Failed WPs** (including `elaboration_failed` and `stub_consistency` findings) — re-elaborate:
- For each failed WP ID, read its `{id}_result.json` from `{$2}/work_packages/` (provides
  `name`, `scope`, `estimated_files`) and its entry from `wp_manifest.json` for status context
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
- **Post-deduplication orphan check:** After resolving all duplicate deliverables, scan every
  modified WP. If any WP now has `deliverables: []`, that WP is an orphan. For each orphan:
  1. Identify the WP(s) that received this orphan's former deliverables (the "owner WPs")
  2. Promote the orphan's `files_touched` entries as deliverables to the most relevant owner
     WP (the one with the most scope overlap), selecting at most
     `DELIVERABLE_BOUNDS[1] - len(owner.deliverables)` entries. Any remaining entries stay
     in `files_touched` only.
  3. Merge the orphan's `technical_steps` and `acceptance_criteria` into the owner WP
  4. Remove the orphan WP from the plan and update all `depends_on` references
  5. Update `wp_manifest.json` and `wp_index.json` accordingly
  This is a deduplication side-effect resolved in the same step, not a sizing violation.
  Deliverable count bounds are defined in `schema.py::DELIVERABLE_BOUNDS`.
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

**DAG cycles** — break 2-node mutual cycles; escalate larger:
- If finding contains `cycle_size: 2` and `cycle_edges`:
  - Identify the two WP IDs and their mutual dependency edges
  - Remove the `depends_on` entry from the **higher-numbered WP** (lexicographic sort
    of WP IDs — e.g., P1-A1-WP2 > P1-A1-WP1, so remove P1-A1-WP1 from P1-A1-WP2's deps)
  - Update the WP's `_result.json` and `dep_graph.json` if it exists
  - Count this toward `issues_fixed`
- If finding contains `cycle_size: 3` or higher (or no `cycle_edges`):
  - Escalate as CRITICAL (same as before)
  - Do NOT attempt cycle-breaking for 3+ node cycles

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
stub_consistency + duplicate_deliverables + dep_references). Sizing-violation,
missing-element, malformed-ID, and DAG-cycle findings are excluded from the count
(they are escalated as critical, not fixed). Files-touched overlap findings are in the
`warnings` array and are not actionable.
