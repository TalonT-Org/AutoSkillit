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

Read `src/autoskillit/core/_type_constants.py`. Extract **all** entries from `FEATURE_REGISTRY` exhaustively — do not assume a fixed list.
For each feature, note: `name`, `lifecycle`, `import_package`, `tool_tags`, `skill_categories`,
`default_enabled`.

### Step 1: Launch 6 Parallel Subagents (SINGLE MESSAGE)

**Issue ALL 6 Task calls in a single message.**

---

**D1 — Config Projection** (subagent):

**Finding IDs:** Assign each finding a unique ID using the scheme `FG-D1-{seq:02d}` where
`{seq}` is a zero-padded sequence starting at 01 (e.g., `FG-D1-01`, `FG-D1-02`). Include
the ID in every finding line. For inventory table rows, cite `file:line` for the config
source (e.g., `src/autoskillit/config/defaults.yaml:NN` or
`src/autoskillit/core/_type_constants.py:NN` for the registry entry).

For each feature in `FEATURE_REGISTRY`: do not output any prose between iterations — process all features and return findings as structured text only.
- Parse `src/autoskillit/config/defaults.yaml` `features:` section
- Parse `.autoskillit/config.yaml` `features:` section (if it exists)
- Compute resolved state: `config_override ?? defaults ?? FeatureDef.default_enabled`
- FLAG (WARN): EXPERIMENTAL feature with `default_enabled=True` in registry
- FLAG (WARN): EXPERIMENTAL feature enabled in project config while on a stable/main branch
- FLAG (WARN): expired `sunset_date` values (compare against today's date, if field present)
- Produce inventory table: `ID | FEATURE | LIFECYCLE | DEFAULT | CONFIG | RESOLVED | RISK | file:line`

Return findings as structured text. Do NOT create any files.

---

**D2 — Import Chain Integrity** (subagent):

**Finding IDs:** Assign each finding a unique ID using the scheme `FG-D2-{seq:02d}` (e.g.,
`FG-D2-01`). Every BLOCK and WARN finding **must** include `file:line` — this is mandatory,
not optional.

For each feature's `import_package`: do not output any prose between iterations — return findings as structured text only.
- Grep all `from {package} import` and `import {package}` across `src/` (excluding tests)
- Classify each import site:
  - GUARDED: inside a function body, inside `if TYPE_CHECKING:`, or inside `if is_feature_enabled(...):`
  - UNGUARDED: top-level import in a module that is not itself the feature's package
- FLAG (BLOCK) all UNGUARDED imports with `file:line`
- Ground-truth targets: `tools_kitchen.py`, `_cook.py`, `_prompts.py`, `_fleet.py`

Return findings as structured text. Do NOT create any files.

---

**D3 — Runtime Gate Consistency** (subagent):

**Finding IDs:** Assign each finding a unique ID using the scheme `FG-D3-{seq:02d}` (e.g.,
`FG-D3-01`). Every BLOCK and WARN finding **must** include `file:line`.

For each feature: do not output any prose between iterations — return findings as structured text only.
- Find all `is_feature_enabled("{name}"` call sites across `src/`
- Find all `AUTOSKILLIT_FEATURES__{NAME}` env-var reads (bypass paths)
- FLAG (BLOCK): env-var gate without a corresponding `is_feature_enabled()` in the same code path
- FLAG (WARN): `_fleet_auto_gate_boot()` calling `mcp.enable()` without then calling `_redisable_subsets()` (`server/_lifespan.py`)
- FLAG (BLOCK): tool handlers for feature-tagged tools without an in-handler `is_feature_enabled()` check (e.g., `dispatch_food_truck` in `server/tools_execution.py`)
- FLAG (WARN): session-type checks that enable feature functionality without verifying the feature flag

Return findings as structured text. Do NOT create any files.

---

**D4 — Tool/Skill Tag Completeness** (subagent):

**Finding IDs:** Assign each finding a unique ID using the scheme `FG-D4-{seq:02d}` (e.g.,
`FG-D4-01`). Every BLOCK and WARN finding **must** include `file:line`.

For each feature: do not output any prose between iterations — return findings as structured text only.
- Cross-reference `feature_def.tool_tags` against `TOOL_SUBSET_TAGS` in `src/autoskillit/core/_type_constants.py`
- Grep skill bodies in `src/autoskillit/skills_extended/` for feature-specific references
- FLAG (WARN): skills with feature references in body but missing the feature's category in frontmatter
- Verify `_DISPLAY_CATEGORIES` in `cli/_cook.py` applies feature-check filtering before displaying
- Verify `list_recipes` in `server/tools_recipe.py` filters `kind: campaign` when fleet is disabled
- FLAG (WARN): any `run_python` callable in `skill_contracts.yaml` whose package matches `feature_def.import_package` without a feature gate in the execution path

Return findings as structured text. Do NOT create any files.

---

**D5 — Boundary Coupling** (subagent):

**Finding IDs:** Assign each finding a unique ID using the scheme `FG-D5-{seq:02d}` (e.g.,
`FG-D5-01`). For WARN/BLOCK coupling table rows, cite `file:line` for the import site or
field declaration (e.g., `src/autoskillit/pipeline/context.py:NN`).

For each feature:
- Grep `src/autoskillit/core/` (IL-0) for feature-specific constants or imports beyond `FeatureDef`/`FEATURE_REGISTRY`
- Check `src/autoskillit/pipeline/context.py` for feature-specific fields unconditionally allocated on `ToolContext`
- Check `src/autoskillit/config/settings.py` for feature-specific config dataclasses parsed without a validation gate
- Check `src/autoskillit/execution/headless.py` for unconditional reads of feature config
- Check `src/autoskillit/server/_factory.py` for unconditional feature-object allocation
- Produce coupling table: `ID | LAYER | FEATURE | COUPLING TYPE | SEVERITY | file:line`

Return findings as structured text. Do NOT create any files.

---

**D6 — Test Marker Coverage** (subagent):

**Finding IDs:** Assign each finding a unique ID using the scheme `FG-D6-{seq:02d}` (e.g.,
`FG-D6-01`). Every WARN finding **must** include `file:line` (the test file missing the marker).

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

Ensure `{{AUTOSKILLIT_TEMP}}/audit-feature-gates/` exists (`mkdir -p`). All paths below are
relative to the current working directory.

Write report to:
`{{AUTOSKILLIT_TEMP}}/audit-feature-gates/feature_gate_audit_{YYYY-MM-DD_HHMMSS}.md`

Report format:

```markdown
# Feature Gate Audit

**Date:** {YYYY-MM-DD HH:MM:SS}  **Features audited:** {comma-separated list from FEATURE_REGISTRY}

## Config Projection

{D1 findings with BLOCK/WARN/INFO severity badges and FG-D1-NN IDs}

### D1 Remediation Checklist

- [ ] [{ID}] {one-line action to resolve the finding}

## Import Chain Integrity

{D2 findings with BLOCK/WARN/INFO severity badges and FG-D2-NN IDs}

### D2 Remediation Checklist

- [ ] [{ID}] {one-line action to resolve the finding}

## Runtime Gate Consistency

{D3 findings with BLOCK/WARN/INFO severity badges and FG-D3-NN IDs}

### D3 Remediation Checklist

- [ ] [{ID}] {one-line action to resolve the finding}

## Tool/Skill Tag Completeness

{D4 findings with BLOCK/WARN/INFO severity badges and FG-D4-NN IDs}

### D4 Remediation Checklist

- [ ] [{ID}] {one-line action to resolve the finding}

## Boundary Coupling

{D5 findings with BLOCK/WARN/INFO severity badges and FG-D5-NN IDs}

### D5 Remediation Checklist

- [ ] [{ID}] {one-line action to resolve the finding}

## Test Marker Coverage

{D6 findings with BLOCK/WARN/INFO severity badges and FG-D6-NN IDs}

### D6 Remediation Checklist

- [ ] [{ID}] {one-line action to resolve the finding}

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
