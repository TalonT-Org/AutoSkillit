---
name: planner-extract-domain
categories:
- planner
description: Extract domain knowledge and naming conventions for planning context
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: planner-extract-domain] Extracting domain knowledge...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
    for_each: selected_domain_exploration_task_ids
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: delegated-worker
    model_class: sonnet
---

# planner-extract-domain

Extract domain knowledge, naming conventions, and structural patterns specific to the project. Optional step — failure is non-fatal and the planner recipe continues without domain context.

## When to Use

- Invoked by the planner recipe after `planner-analyze` completes
- Provides richer domain context for decomposition planning

## Arguments

- **$1** — Absolute path to `analysis.json` produced by `planner-analyze`
- **$2** — Absolute path to a file containing the task description. When provided and non-empty, focus domain extraction on areas relevant to the stated task. When empty, perform a full-codebase survey.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any target project files
- Abort the calling recipe on failure — log a warning and return gracefully
- Detach child delegations instead of joining them (joining every child is required)
- If `$1` is empty or the file does not exist, STOP immediately and report failure
- Start independent child delegations sequentially

- Write, Edit, or use file-modifying Bash commands (sed -i, echo >, tee) on any file outside the planner output directory ($AUTOSKILLIT_ALLOWED_WRITE_PREFIX). Source code files must NEVER be modified.

**ALWAYS:**
- Read the analysis file from argument $1 before spawning subagents
- Use the registered exploration roles for all repository reads
- Dispatch ready, scope-disjoint vectors concurrently
- Start all independent child delegations in a single message before awaiting any result
- Wait for every dispatched exploration result before synthesis

## Workflow

### Step 1: Read analysis

Read the `analysis.json` file from argument $1. Use its `language`, `framework`, `architecture_style`, and `key_patterns` fields to focus subagent queries.

### Step 2: Launch 4–7 parallel exploration vectors

Set `selected_domain_exploration_task_ids` to the four always-applicable task IDs plus
the deep task IDs only when their module-count or architecture-style predicate holds.
Use that exact collection for dispatch and joins.

Dispatch all ready, scope-disjoint vectors together. Do not iterate across multiple turns.

Start all independent child delegations before awaiting any result, then join every child before synthesis.

Do not output prose between dispatches. Immediately proceed to the next vector.

Read the task description: if $2 is provided and non-empty, read the file at that path.

If the task description is available, include it in each subagent's prompt: "Focus exploration on
domain vocabulary, abstractions, and integration points relevant to this task: {task}.
Prioritize areas the task will touch over exhaustive full-codebase coverage."

Dispatch all applicable vectors through the deterministic router. Always dispatch vectors 1–4; dispatch vectors 5–7 only when the project has >20 modules or `architecture_style` is layered/hexagonal:

<!-- autoskillit:exploration-vector id="domain-vocabulary" -->
1. **Domain Vocabulary** — Extract domain-specific terms, entity names, and verb patterns used in identifiers. Look for: class names, function names, docstrings, README files, ADR documents.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="existing-abstractions" -->
2. **Existing Abstractions** — Identify base classes, protocols, ABCs, and reusable interfaces. Look for: `class * (Protocol)`, `ABC`, the `abstractmethod` decorator, shared base types.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="integration-points" -->
3. **Integration Points** — Identify external system boundaries, HTTP clients, database adapters, message queues. Look for: import of third-party HTTP/DB libraries, adapter classes, port/adapter naming.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="integration-consumers" -->
4. **Integration Consumers** — Identify configuration, registries, manifests, tests, fixtures, and generated consumers that bind or verify those integration points.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="cross-cutting-concerns" -->
5. **Cross-cutting Concerns** (deep mode) — Identify async patterns, error handling conventions, and logging control flow. Look for: `async def`, custom exception hierarchies, structured logging calls.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="data-flow-patterns" -->
6. **Data Flow Patterns** (deep mode) — Identify pipeline stages, transformation chains, data schemas. Look for: dataclass chains, TypedDict, Pydantic models, transformation functions.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="cross-cutting-impact" -->
7. **Cross-cutting Impact** (deep mode) — Identify logging/error configuration, registries, tests, fixtures, generated artifacts, and declarative consumers that constrain these patterns.
<!-- /autoskillit:exploration-vector -->

### Step 3: Synthesize

Merge all agent outputs into a coherent `domain_knowledge.md` Markdown document with sections: Domain Vocabulary, Key Abstractions, Integration Points, Cross-cutting Concerns, Data Flow Patterns.

### Step 4: Write output (non-fatal)

Write to `$(dirname $1)/domain_knowledge.md`. If any step fails, log a warning to stdout and exit with code 0 — do not propagate the error to the recipe.
