---
name: planner-reconcile-deps
categories: [planner]
description: Post-Pass-3 global dependency DAG construction — detects implicit dependencies individual WP sessions could not see
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-reconcile-deps] Reconciling dependency graph...'"
          once: true
---

# planner-reconcile-deps

Post-Pass-3 global dependency analysis. Reads the full `wp_index.json` (compact, ~12k
tokens for 60 WPs) and produces a corrected global dependency DAG. Detects implicit
dependencies that individual WP sessions could not see — forward references, transitive
chains, and API consumption patterns missed during per-WP elaboration.

Runs as a single LLM session for holistic cross-WP reasoning that per-WP sessions cannot
perform. The compact index fits entirely in context, enabling global analysis without
spawning parallel sessions.

## When to Use

- Invoked by the planner recipe immediately after Pass 3 completes
- One invocation per plan — processes the complete WP set

## Arguments

- **$1** — Absolute path to the planner output directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner`)

## Critical Constraints

**NEVER:**
- Load full WP result files — only `wp_index.json` (the compact array)
- Spawn additional sessions — this skill must run as a single session for holistic reasoning
- Write output outside `{$1}/`
- Add transitive deps without a concrete API or file relationship as evidence

**ALWAYS:**
- Read `wp_index.json` in full before analyzing
- Populate `added_backward_deps` only when an `apis_consumed`/`apis_defined` or `files_touched` match justifies the dependency
- Emit `dep_graph_path` as the output token

## Workflow

### Step 1: Load wp_index.json

Read `{$1}/work_packages/wp_index.json`. This JSON array contains compact entries for
all WPs (~200 bytes each).

Each entry has: `id`, `name`, `summary`, `phase`, `assignment`, `files_touched`,
`apis_defined`, `apis_consumed`, `depends_on`, `deliverables`, `result_path`.

### Step 2: Analyze dependency relationships

Scan the full WP array holistically for:

1. **Implicit backward dependencies** — A WP has an `apis_consumed` entry that matches
   another WP's `apis_defined`, but the consuming WP does not list the defining WP in
   `depends_on`. These are missing backward deps that go into `added_backward_deps`.

2. **File-level implicit deps** — A WP's `files_touched` overlaps with a prior WP's
   `deliverables`, but no `depends_on` relationship exists. The later WP implicitly
   depends on the earlier.

3. **Forward dependency map** — For each WP, compute which other WPs depend on it
   (transpose of `depends_on`). This produces the `forward_deps` map.

4. **Reverse dependency consolidation** — Collect the existing `depends_on` arrays into
   the `reverse_deps` map for downstream consumers (e.g., `compile_plan`).

### Step 3: Build dep_graph.json

```json
{
  "forward_deps": {
    "P1-A1-WP1": ["P1-A2-WP1", "P2-A1-WP1"]
  },
  "reverse_deps": {
    "P1-A2-WP1": ["P1-A1-WP1", "P1-A1-WP2"]
  },
  "added_backward_deps": {
    "P2-A3-WP1": ["P1-A2-WP2"]
  }
}
```

- `forward_deps`: For each WP, list all WPs that depend on it (forward in execution order)
- `reverse_deps`: For each WP, list the WPs it currently depends on (mirrors `depends_on` arrays)
- `added_backward_deps`: New deps not declared in original `depends_on` — only when a concrete API/file match justifies the relationship

If no implicit deps are detected, write `added_backward_deps: {}`.

### Step 4: Write dep_graph.json

Write to `{$1}/dep_graph.json`.

### Step 5: Emit output token

```
dep_graph_path = <absolute path to dep_graph.json>
```
