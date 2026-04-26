---
name: planner-extract-domain
categories: [planner]
description: Extract domain knowledge and naming conventions for planning context
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-extract-domain] Extracting domain knowledge...'"
          once: true
---

# planner-extract-domain

Extract domain knowledge, naming conventions, and structural patterns specific to the project. Optional step — failure is non-fatal and the planner recipe continues without domain context.

## When to Use

- Invoked by the planner recipe after `planner-analyze` completes
- Provides richer domain context for decomposition planning

## Arguments

- **`PLANNER_ANALYSIS_FILE`** (env-var) — Absolute path to `analysis.json` produced by `planner-analyze`. Set by the recipe via the step's `env:` block.

## Critical Constraints

**NEVER:**
- Modify any target project files
- Abort the calling recipe on failure — log a warning and return gracefully

**ALWAYS:**
- Read the analysis file from the path in the PLANNER_ANALYSIS_FILE environment variable before spawning subagents
- Use Explore subagents for all file reads
- Spawn subagents in parallel

## Workflow

### Step 1: Read analysis

Read the `analysis.json` file from the path in the PLANNER_ANALYSIS_FILE environment variable. Use its `language`, `framework`, `architecture_style`, and `key_patterns` fields to focus subagent queries.

### Step 2: Launch 3–5 parallel Explore subagents

Spawn all concurrently with `model: "sonnet"`. Always spawn agents 1–3; spawn agents 4–5 only when the project has >20 modules or architecture_style is layered/hexagonal:

1. **Domain Vocabulary** — Extract domain-specific terms, entity names, and verb patterns used in identifiers. Look for: class names, function names, docstrings, README files, ADR documents.

2. **Existing Abstractions** — Identify base classes, protocols, ABCs, and reusable interfaces. Look for: `class * (Protocol)`, `ABC`, the `abstractmethod` decorator, shared base types.

3. **Integration Points** — Identify external system boundaries, HTTP clients, database adapters, message queues. Look for: import of third-party HTTP/DB libraries, adapter classes, port/adapter naming.

4. **Cross-cutting Concerns** (deep mode) — Identify async patterns, error handling conventions, logging strategy. Look for: `async def`, custom exception hierarchies, structured logging calls.

5. **Data Flow Patterns** (deep mode) — Identify pipeline stages, transformation chains, data schemas. Look for: dataclass chains, TypedDict, Pydantic models, transformation functions.

### Step 3: Synthesize

Merge all agent outputs into a coherent `domain_knowledge.md` Markdown document with sections: Domain Vocabulary, Key Abstractions, Integration Points, Cross-cutting Concerns, Data Flow Patterns.

### Step 4: Write output (non-fatal)

Write to `{{AUTOSKILLIT_TEMP}}/planner/domain_knowledge.md` (relative to the current working directory). If any step fails, log a warning to stdout and exit with code 0 — do not propagate the error to the recipe.
