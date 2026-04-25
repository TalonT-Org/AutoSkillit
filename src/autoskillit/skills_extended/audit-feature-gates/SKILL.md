---
name: audit-feature-gates
categories: [audit]
description: >
  Audit feature flag isolation — traces import chains, runtime gates, tool/skill
  tag coverage, UI surfaces, and test markers to detect leakage and miswiring.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: audit-feature-gates] Read-only audit — no code changes permitted'"
          once: true
---

# Audit Feature Gates Skill

Audits feature flag isolation across 6 dimensions: config projection, import chain integrity,
runtime gate consistency, tool/skill tag completeness, boundary coupling, and test marker
coverage. Detects where disabled features leak through import chains, runtime bypasses, UI
surfaces, and ungated callables.

## When to Use

- User says "audit feature gates", "check feature isolation", "feature flag audit"
- After adding a new feature to `FEATURE_REGISTRY` to verify isolation is complete
- As part of the `full-audit.yaml` 4th parallel chain

## Arguments

No arguments required. Reads `FEATURE_REGISTRY` from `src/autoskillit/core/_type_constants.py`
to enumerate features.

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `{{AUTOSKILLIT_TEMP}}/audit-feature-gates/`
- Issue subagent Task calls sequentially — ALL 6 must be in a single parallel message
- Write output files before synthesizing ALL subagent results

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Issue all 6 Task calls in a single message to maximize parallelism
- Subagents must NOT create their own files — they return findings in response text only

## Severity Semantics

- **BLOCK** — active leakage that would execute feature code when the feature is disabled
- **WARN** — isolation gap that may cause confusion or is one step from leakage
- **INFO** — coupling assessment, informational only

---

## Workflow

### Step 0: Pre-flight — Enumerate Features

Read `src/autoskillit/core/_type_constants.py`. Extract all entries from `FEATURE_REGISTRY`.
For each feature, note: `name`, `lifecycle`, `import_package`, `tool_tags`, `skill_categories`,
`default_enabled`. Current known features: `fleet`, `planner`.

### Step 1: Launch 6 Parallel Subagents (SINGLE MESSAGE)

**Issue ALL 6 Task calls in a single message.**

---

**D1 — Config Projection** (subagent):

For each feature in `FEATURE_REGISTRY`:
- Parse `src/autoskillit/config/defaults.yaml` `features:` section
- Parse `.autoskillit/config.yaml` `features:` section (if it exists)
- Compute resolved state: `config_override ?? defaults ?? FeatureDef.default_enabled`
- FLAG (WARN): EXPERIMENTAL feature with `default_enabled=True` in registry
- FLAG (WARN): EXPERIMENTAL feature enabled in project config while on a stable/main branch
- FLAG (WARN): expired `sunset_date` values (compare against today's date, if field present)
- Produce inventory table: `FEATURE | LIFECYCLE | DEFAULT | CONFIG | RESOLVED | RISK`

Return findings as structured text. Do NOT create any files.

---

**D2 — Import Chain Integrity** (subagent):

For each feature's `import_package`:
- Grep all `from {package} import` and `import {package}` across `src/` (excluding tests)
- Classify each import site:
  - GUARDED: inside a function body, inside `if TYPE_CHECKING:`, or inside `if is_feature_enabled(...):`
  - UNGUARDED: top-level import in a module that is not itself the feature's package
- FLAG (BLOCK) all UNGUARDED imports with `file:line`
- Ground-truth targets: `tools_kitchen.py`, `_cook.py`, `_prompts.py`, `_fleet.py`

Return findings as structured text. Do NOT create any files.

---

**D3 — Runtime Gate Consistency** (subagent):

For each feature:
- Find all `is_feature_enabled("{name}"` call sites across `src/`
- Find all `AUTOSKILLIT_FEATURES__{NAME}` env-var reads (bypass paths)
- FLAG (BLOCK): env-var gate without a corresponding `is_feature_enabled()` in the same code path
- FLAG (WARN): `_fleet_auto_gate_boot()` calling `mcp.enable()` without then calling `_redisable_subsets()` (`server/_lifespan.py`)
- FLAG (BLOCK): tool handlers for feature-tagged tools without an in-handler `is_feature_enabled()` check (e.g., `dispatch_food_truck` in `server/tools_execution.py`)
- FLAG (WARN): session-type checks that enable feature functionality without verifying the feature flag

Return findings as structured text. Do NOT create any files.

---

**D4 — Tool/Skill Tag Completeness** (subagent):

For each feature:
- Cross-reference `feature_def.tool_tags` against `TOOL_SUBSET_TAGS` in `src/autoskillit/core/_type_constants.py`
- Grep skill bodies in `src/autoskillit/skills_extended/` for feature-specific references
- FLAG (WARN): skills with feature references in body but missing the feature's category in frontmatter
- Ground-truth target: `sprint-planner` skill — check if `categories: [planner]` is present
- Verify `_DISPLAY_CATEGORIES` in `cli/_cook.py` applies feature-check filtering before displaying
- Verify `list_recipes` in `server/tools_recipe.py` filters `kind: campaign` when fleet is disabled
- FLAG (WARN): any `run_python` callable in `skill_contracts.yaml` whose package matches `feature_def.import_package` without a feature gate in the execution path

Return findings as structured text. Do NOT create any files.

---

**D5 — Boundary Coupling** (subagent):

For each feature:
- Grep `src/autoskillit/core/` (L0) for feature-specific constants or imports beyond `FeatureDef`/`FEATURE_REGISTRY`
- Check `src/autoskillit/pipeline/context.py` for feature-specific fields unconditionally allocated on `ToolContext`
- Check `src/autoskillit/config/settings.py` for feature-specific config dataclasses parsed without a validation gate
- Check `src/autoskillit/execution/headless.py` for unconditional reads of feature config
- Check `src/autoskillit/server/_factory.py` for unconditional feature-object allocation
- Produce coupling table: `LAYER | FEATURE | COUPLING TYPE | SEVERITY`

Return findings as structured text. Do NOT create any files.

---

**D6 — Test Marker Coverage** (subagent):

For each feature:
- Find test files importing from `feature.import_package` or referencing feature-specific symbols (Grep `tests/` for the import_package name)
- Verify each such test file carries `pytest.mark.feature("{name}")` at file or class level
- Check `tests/arch/test_feature_markers.py` for per-feature enforcement lists
- FLAG (WARN): features with no marker enforcement list in `test_feature_markers.py`
- FLAG (WARN): test files with feature code but no `feature("{name}")` marker
- Ground-truth: check for test files with fleet code but no `feature("fleet")` marker; check if planner marker enforcement exists in `test_feature_markers.py`

Return findings as structured text. Do NOT create any files.

---

### Step 2: Synthesize and Assemble Report

After all 6 subagents return, consolidate findings per dimension. Count BLOCK/WARN/INFO totals.

Ensure `{{AUTOSKILLIT_TEMP}}/audit-feature-gates/` exists (`mkdir -p`).

Write report to:
`{{AUTOSKILLIT_TEMP}}/audit-feature-gates/feature_gate_audit_{YYYY-MM-DD_HHMMSS}.md`

Report format:

```markdown
# Feature Gate Audit

**Date:** {YYYY-MM-DD HH:MM:SS}  **Features audited:** fleet, planner

## Config Projection

{D1 findings with BLOCK/WARN/INFO severity badges}

## Import Chain Integrity

{D2 findings with BLOCK/WARN/INFO severity badges}

## Runtime Gate Consistency

{D3 findings with BLOCK/WARN/INFO severity badges}

## Tool/Skill Tag Completeness

{D4 findings with BLOCK/WARN/INFO severity badges}

## Boundary Coupling

{D5 findings with BLOCK/WARN/INFO severity badges}

## Test Marker Coverage

{D6 findings with BLOCK/WARN/INFO severity badges}

## Summary

| Severity | Count |
|----------|-------|
| BLOCK    | N     |
| WARN     | N     |
| INFO     | N     |
| **Total**| N     |
```

### Step 3: Print Handoff

Print to terminal:

```
[audit-feature-gates] Done.
  BLOCK: {N} | WARN: {N} | INFO: {N}
  Report: {AUTOSKILLIT_TEMP}/audit-feature-gates/feature_gate_audit_{ts}.md
```

---

## Output Location

```
{{AUTOSKILLIT_TEMP}}/audit-feature-gates/
└── feature_gate_audit_{YYYY-MM-DD_HHMMSS}.md
```

## Related Skills

- `/autoskillit:validate-audit` — validates the report produced by this skill
- `/autoskillit:audit-arch` — parallel audit chain in `full-audit.yaml`
- `/autoskillit:audit-tests` — parallel audit chain in `full-audit.yaml`
- `/autoskillit:audit-cohesion` — parallel audit chain in `full-audit.yaml`
