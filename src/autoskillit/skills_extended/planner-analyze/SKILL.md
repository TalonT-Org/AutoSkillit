---
name: planner-analyze
categories: [planner]
description: Analyze project structure for planning decomposition
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-analyze] Analyzing project structure...'"
          once: true
---

# planner-analyze

Detect language, framework, test infrastructure, project structure, and existing patterns in the target project. Produces `analysis.json` used by subsequent planner skills.

## When to Use

- Invoked by the planner recipe as the first analysis step
- User says "analyze project structure" in a planning context

## Arguments

- **$1** — Absolute path to the run-scoped planner directory (e.g., `/path/to/.autoskillit/temp/planner/run-YYYYMMDD-HHMMSS`). Created by the `init` step.

## Critical Constraints

**NEVER:**
- Modify any target project files
- Write analysis.json outside `$1/`

**ALWAYS:**
- Use Explore subagents for all file reads
- Spawn all 4 subagents in parallel
- Write valid JSON to `analysis.json`

## Workflow

### Step 1: Launch 4 parallel Explore subagents

Spawn all four concurrently with `model: "sonnet"`:

1. **Languages & Frameworks** — Identify primary language, framework, build system. Look for: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, import statements, dependency files.

2. **Test Infrastructure** — Identify test runner, coverage tools, test directory layout. Look for: `pytest.ini`, `jest.config.*`, `go test`, `cargo test`, test file naming patterns.

3. **Architecture Patterns** — Identify architecture style (layered, hexagonal, monolithic, microservices, etc.) and count modules. Look for: directory depth, import graphs, layer naming, package boundaries.

4. **Existing Conventions** — Identify naming conventions, code patterns, and risk areas. Look for: consistent naming in identifiers, repeated structural patterns, areas with high coupling or missing tests.

### Step 2: Synthesize results

Merge all four agent outputs into a single `analysis.json` document matching the output schema.

### Step 3: Write output

Write to `$1/analysis.json`. The directory was created by the `init` step.

## Output Schema

```json
{
  "language": "python",
  "framework": "fastapi",
  "build_system": "uv",
  "test_runner": "pytest",
  "architecture_style": "layered",
  "module_count": 42,
  "key_patterns": ["dependency injection", "protocol-based contracts"],
  "conventions": ["snake_case identifiers", "private prefix _"],
  "risks": ["high coupling in server layer", "no tests for migration engine"]
}
```

All fields are required. Use `null` for fields that cannot be determined. Arrays may be empty but must be present.
