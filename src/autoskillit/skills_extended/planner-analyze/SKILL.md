---
name: planner-analyze
categories:
- planner
description: Analyze project structure for planning decomposition
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: planner-analyze] Analyzing project structure...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
    count: 1
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

# planner-analyze

> **Preflight:** Before acting on any `exploration-vector` directive below, call `enable_exploration` to establish read-only broker authority for this session; the vectors below assume broker access has already been granted.

Detect language, framework, test infrastructure, project structure, and existing patterns in the target project. Produces `analysis.json` used by subsequent planner skills.

## When to Use

- Invoked by the planner recipe as the first analysis step
- User says "analyze project structure" in a planning context

## Arguments

- **$1** — Absolute path to the run-scoped planner directory (e.g., `{{AUTOSKILLIT_TEMP}}/planner/run-YYYYMMDD-HHMMSS`). Created by the `init` step.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any target project files
- Write analysis.json outside `$1/`
- Detach child delegations instead of joining them (joining every child is required)
- Run exploration leaves in the background
- Start independent child delegations sequentially

- Write, Edit, or use file-modifying Bash commands (sed -i, echo >, tee) on any file outside the planner output directory ($AUTOSKILLIT_ALLOWED_WRITE_PREFIX). Source code files must NEVER be modified.

**ALWAYS:**
- Use the registered exploration roles for all repository reads
- Dispatch all 5 applicable vectors through the deterministic router
- Start all independent child delegations in a single message before awaiting any result
- Write valid JSON to `analysis.json`
- Wait for every exploration result before synthesis

## Workflow

### Step 1: Launch 5 routed exploration vectors

Dispatch all ready, scope-disjoint vectors in a single message before awaiting any result.

Do not output prose between dispatches. Immediately proceed to the next vector.

Dispatch all five concurrently under their registered role policies:

<!-- autoskillit:exploration-vector id="languages-frameworks" -->
1. **Languages & Frameworks** — Identify primary language, framework, build system. Look for: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, import statements, dependency files.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="test-infrastructure" -->
2. **Test Infrastructure** — Identify test runner, coverage tools, test directory layout. Look for: `pytest.ini`, `jest.config.*`, `go test`, `cargo test`, test file naming patterns.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="architecture-patterns" -->
3. **Architecture Patterns** — Identify architecture style (layered, hexagonal, monolithic, microservices, etc.) and count modules. Look for: directory depth, import graphs, layer naming, package boundaries.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="existing-conventions" -->
4. **Existing Conventions** — Identify naming conventions and code patterns. Look for: consistent naming in identifiers and repeated structural patterns.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="existing-conventions-impact" -->
5. **Convention impact and risk evidence** — Identify tests, configuration consumers, generated artifacts, high-coupling areas, and missing verification associated with the established patterns.
<!-- /autoskillit:exploration-vector -->

### Step 2: Synthesize results

Merge all exploration agent outputs into a single `analysis.json` document matching the output schema.

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
