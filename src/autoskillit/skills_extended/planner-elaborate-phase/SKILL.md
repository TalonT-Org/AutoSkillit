---
name: planner-elaborate-phase
categories:
- planner
description: Elaborate a single phase into a full result, parallel-safe — receives plan snapshot + target phase ID
exploration_vectors:
  - id: affected-files
    disposition: migrated
    rationale: Semantic navigation covers affected source modules, symbols, imports, and structural deviations across the phase scope.
    applicability: always
    role: semantic-code-navigator
    profile: auto
    relationship_classes: [defines, imports, references]
    task_id: planner-elaborate-phase-affected-files
    frontier_item_id: planner-elaborate-phase-affected-files-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: affected-file-impact
    disposition: migrated
    rationale: Repository impact evidence covers configuration, registries, artifacts, tests, fixtures, and downstream consumers for the affected scope.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [references, affects]
    task_id: planner-elaborate-phase-affected-file-impact
    frontier_item_id: planner-elaborate-phase-affected-file-impact-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: dependency-analysis
    disposition: migrated
    rationale: Semantic navigation covers import, call, and consumer relationships for affected modules.
    applicability: always
    role: semantic-code-navigator
    profile: auto
    relationship_classes: [imports, calls, references]
    task_id: planner-elaborate-phase-dependencies
    frontier_item_id: planner-elaborate-phase-dependencies-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: test-coverage
    disposition: migrated
    rationale: Repository impact evidence covers tests, coverage surfaces, and downstream verification gaps.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [references, affects]
    task_id: planner-elaborate-phase-test-coverage
    frontier_item_id: planner-elaborate-phase-test-coverage-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: pattern-discovery
    disposition: migrated
    rationale: Repository impact evidence covers reusable utilities, conventions, tests, fixtures, and artifact consumers within the affected scope.
    applicability: always
    role: repository-impact-profiler
    profile: auto
    relationship_classes: [references, affects]
    task_id: planner-elaborate-phase-patterns
    frontier_item_id: planner-elaborate-phase-patterns-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: cross-phase-boundaries
    disposition: migrated
    rationale: Semantic navigation covers dependency direction and structural handoff surfaces while the parent retains cross-phase synthesis.
    applicability: always
    role: semantic-code-navigator
    profile: auto
    relationship_classes: [imports, calls, references]
    task_id: planner-elaborate-phase-boundaries
    frontier_item_id: planner-elaborate-phase-boundaries-frontier
    depends_on: []
    scope: [.]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: planner-elaborate-phase] Elaborating phase...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
---

# planner-elaborate-phase

Standalone parallel worker for Phase Pass 1. Each instance receives the full plan
snapshot (each phase in condensed form) and a target phase ID. It explores the codebase
independently and writes a single elaborated phase result. No dependency on
`check_remaining` or any shared state machine.

## When to Use

- Launched in parallel by the L2 orchestrator (planner recipe, Issue 08)
- One instance per phase ID, all running simultaneously
- Also usable standalone for manual single-phase elaboration

## Arguments

- **$1** — Absolute path to `plan_snapshot.json` (every phase as a `PhaseShort` entry)
- **$2** — Phase ID to elaborate (e.g., `"P3"`)
- **$3** — Absolute path to output directory (result written here)

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Write output outside `$3/`
- Read any `*_result.json` file from other phases (you have only the snapshot)
- Require or read a context file from `check_remaining`
- Communicate with other parallel worker instances
- Read `{{AUTOSKILLIT_TEMP}}` artifacts outside your designated input files and output directory
- Explore parent directories of your input paths (e.g., `ls $(dirname $1)/..`)
- Detach child delegations instead of joining them (joining every child is required)
- Run exploration leaves in the background
- Start independent child delegations sequentially

- Write, Edit, or use file-modifying Bash commands (sed -i, echo >, tee) on any file outside the planner output directory ($AUTOSKILLIT_ALLOWED_WRITE_PREFIX). Source code files must NEVER be modified.

**ALWAYS:**
- Derive `relationship_notes` from snapshot context + codebase analysis, NOT from prior result files
- Write result to `$3/{phase_id}_result.json` (keep `_result.json` suffix — downstream consumers glob `*_result.json`)
- Emit: `elab_result_path = <absolute path to {phase_id}_result.json>`
- Include all `PhaseElaborated` fields in the result
- Dispatch all ready, scope-disjoint vectors through the deterministic router before awaiting any result, then join every result
- Start all independent child delegations in a single message before awaiting any result

## Workflow

### Step 1: Parse arguments and read snapshot

Read the plan snapshot at `$1`. It is a `PlanDocument` with a `phases` list of `PhaseShort` objects:
```json
{
  "schema_version": 1,
  "task": "...",
  "source_dir": "...",
  "phases": [
    {"id": "P1", "name": "...", "goal": "...", "scope": [...], "ordering": 1},
    {"id": "P2", "name": "...", "goal": "...", "scope": [...], "ordering": 2},
    ...
  ]
}
```

Find the entry in `phases` where `id == "$2"` (the target phase). Note its `ordering` to
understand which phases come before and after it.

After reading `plan_snapshot.json`, extract the `task` field. Every aspect of the elaborated
phase — its `technical_approach`, `scope`, and `assignments[]` — must serve the stated task.
Do not elaborate into work not requested by the task. Flag if the phase goal appears
unrelated to the task.

### Step 2: Launch parallel codebase exploration vectors

Dispatch all ready, scope-disjoint vectors together. Do not iterate across multiple turns, and join every child before synthesis.

Do not output prose between dispatches. Immediately proceed to the next vector.

Dispatch the 6 exploration vectors through the deterministic router against the codebase in `source_dir`:

<!-- autoskillit:exploration-vector id="affected-files" -->
1. **Affected source structure** — Which source files, modules, and symbols fall within this phase's `scope`? Capture current imports and structural deviations.
<!-- /autoskillit:exploration-vector -->
<!-- autoskillit:exploration-vector id="affected-file-impact" -->
2. **Affected artifact and consumer impact** — Which configuration, registries, generated artifacts, tests, fixtures, and downstream consumers are tied to the affected scope?
<!-- /autoskillit:exploration-vector -->
<!-- autoskillit:exploration-vector id="dependency-analysis" -->
3. **Dependency analysis** — What imports and consumes the affected modules? Full import graph.
<!-- /autoskillit:exploration-vector -->
<!-- autoskillit:exploration-vector id="test-coverage" -->
4. **Test coverage** — Which tests cover the affected scope? Gaps in coverage?
<!-- /autoskillit:exploration-vector -->
<!-- autoskillit:exploration-vector id="pattern-discovery" -->
5. **Pattern discovery** — What conventions, reusable utilities, fixtures, and artifact consumers exist in this scope?
<!-- /autoskillit:exploration-vector -->
<!-- autoskillit:exploration-vector id="cross-phase-boundaries" -->
6. **Cross-phase boundaries** — Based on snapshot context (other phases' names/goals/scopes), where do structural dependencies or handoff points exist?
<!-- /autoskillit:exploration-vector -->

### Step 3: Write phase result

Write to `$3/{target_phase_id}_result.json` matching `PhaseElaborated`:
```json
{
  "id": "P3",
  "name": "...",
  "goal": "...",
  "scope": [...],
  "ordering": 3,
  "technical_approach": "...",
  "relationship_notes": "Depends on P1 (...name...) for ...; P5 (...name...) will consume ...",
  "assignments_preview": ["Assignment title 1", "Assignment title 2", ...]
}
```

For `relationship_notes`: use other phases' `name`, `goal`, and `scope` from the snapshot
(not their result files) combined with codebase evidence to identify real dependencies.

Do NOT write `phase_number` or `name_slug` — the backend derives these at load time from
`ordering` and `name` respectively.

### Step 4: Emit output token

```
elab_result_path = <absolute path to $3/{id}_result.json>
```

## Context Limit Behavior

This skill writes result files to the output directory during execution.
If context is exhausted mid-execution:

1. Commit any pending file writes to disk before exiting.
2. The caller's `on_context_limit` routing handles recovery — do not attempt partial structured output.
