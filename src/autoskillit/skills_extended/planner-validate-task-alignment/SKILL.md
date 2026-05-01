---
name: planner-validate-task-alignment
categories: [planner]
description: Validate that plan phases and WPs align with the stated task
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-validate-task-alignment] Validating task alignment...'"
          once: true
---

# planner-validate-task-alignment

Post-refinement catch-net. Compares the plan's phases, assignments, and WP descriptions
against the original task description. Emits warning-severity findings for misalignment.
This is a safety net — if upstream task injection works correctly, this step should produce
zero findings.

## Arguments

- **$1** — Absolute path to `refined_wps.json` (PlanDocument with `task`, `work_packages[]`)
- **$2** — Absolute path to `refined_plan.json` (PlanDocument with `task`, `phases[]`)
- **$3** — Absolute path to output directory for findings

## Critical Constraints

**NEVER:**
- Block the pipeline — all findings are `warning` severity
- Write output outside `$3/`
- Read files not passed as arguments
- Modify input files
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Read the `task` field from $1 or $2
- Compare every phase goal and every WP description against the task
- Write `$3/task_alignment.json` with findings array
- Emit `alignment_findings_path` and `alignment_finding_count` output tokens

## Workflow

### Step 1: Read inputs

Read `refined_wps.json` from $1 and `refined_plan.json` from $2. Extract:
- The `task` field (the user's original task description)
- All phase `goal` and `scope` fields from $2
- All WP `name`, `deliverables`, and `acceptance_criteria` fields from $1

### Step 2: Spawn alignment-check subagents

Spawn 1-2 subagents (model: "sonnet"):

**Subagent A — Phase alignment:**
Provide the task description and all phase goals/scopes. Ask: "For each phase, does its
goal directly serve the stated task? Rate each phase as 'aligned', 'tangential', or
'unrelated'. A phase is 'aligned' if the task explicitly or implicitly requires the work.
A phase is 'tangential' if it supports the task but was not requested. A phase is 'unrelated'
if the task does not mention or imply this work."

**Subagent B — WP alignment:**
Provide the task description and all WP names/deliverables. Ask: "For each WP, do its
deliverables serve the stated task? Flag any WP whose deliverables address concerns not
mentioned in the task."

### Step 3: Collect and write findings

Parse subagent responses. For each phase or WP rated 'tangential' or 'unrelated', emit
a finding:

```json
{
  "message": "Phase P2 ('Protocol Sharding') appears unrelated to the stated task",
  "severity": "warning",
  "check": "task_alignment",
  "entity_id": "P2",
  "rating": "unrelated"
}
```

Write all findings to `$3/task_alignment.json`:
```json
{
  "schema_version": 1,
  "task": "<original task>",
  "findings": [],
  "summary": {
    "aligned": 4,
    "tangential": 1,
    "unrelated": 0
  }
}
```

### Step 4: Emit output tokens

```
alignment_findings_path = <absolute path to $3/task_alignment.json>
alignment_finding_count = <N>
```
