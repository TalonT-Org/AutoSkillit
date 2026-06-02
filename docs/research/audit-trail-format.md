# Audit Trail Format

Documents the structure of `research/{slug}/audit/` artifacts produced by the
research recipe pipeline.

## Directory Structure

```
research/{YYYY-MM-DD}-{slug}/
├── audit/
│   ├── design-review-dashboard.md    ← copied from AUTOSKILLIT_TEMP/apply-review-dimensions/
│   └── visualization-plan-trace.md   ← copied from AUTOSKILLIT_TEMP/plan-visualization/
├── report.md                         ← references audit/ via YAML frontmatter
├── scripts/
│   └── ...
└── data/
    └── ...
```

## Artifact Descriptions

### `audit/design-review-dashboard.md`

**Source:** `{{AUTOSKILLIT_TEMP}}/apply-review-dimensions/evaluation_dashboard_{slug}_{timestamp}.md`
**Copied by:** `scripts/recipe/create_worktree.sh`
**When:** During the `create_worktree` recipe step

Contains:
- Verdict banner (GO / REVISE / STOP)
- Classification summary (experiment type, methodology tradition)
- Dimension rationale (per-dimension weight table)
- Dimension scorecard (dimension → weight → findings → severity)
- Adversarial (red-team) findings
- "Cannot Assess" entries
- Mechanizable check log
- Machine-readable YAML summary block
- Silent-type advisories (appended when applicable — see `silent-type-convention.md`)

### `audit/visualization-plan-trace.md`

**Source:** `{{AUTOSKILLIT_TEMP}}/synthesize-vis-plan/visualization-plan-trace.md`
**Copied by:** `scripts/recipe/create_worktree.sh`
**When:** During the `create_worktree` recipe step

Contains:
- `primary_tradition` — the methodology tradition selected by Tier-C routing
- `applied_union_rules` — which union rules were applied (from disambiguation)
- `precedence_trace` — the precedence resolution chain
- `tier_c_lens` — which vis-lens was dispatched (e.g., `vis-lens-methodology-norms`)
- `disambiguation_rule_applied` — which disambiguation rule fired, or null
- Silent-type advisories (appended when applicable — see `silent-type-convention.md`)

## Report Frontmatter Reference

`report.md` references audit artifacts via YAML frontmatter:

```yaml
---
experiment_type: causal_inference
methodology_traditions:
  - controlled_intervention
disambiguation_rule_applied: null
tier_c_lens: vis-lens-methodology-norms
design_review_verdict: GO
classification_timestamp: "2026-04-13T15:32:00Z"
audit_trail_path:
  design_review: research/{slug}/audit/design-review-dashboard.md
  visualization_trace: research/{slug}/audit/visualization-plan-trace.md
---
```

## Lifecycle

1. **Ephemeral:** Artifacts are written to `{{AUTOSKILLIT_TEMP}}/` during recipe execution
2. **Persisted:** `create_worktree.sh` copies them to `research/{slug}/audit/`
3. **Committed:** The `git add research/ && git commit` at the end of `create_worktree.sh` commits them
4. **Referenced:** `generate-report` writes relative paths into the report frontmatter
5. **Archived:** When the research bundle is archived (PR or local export), audit artifacts travel with it
