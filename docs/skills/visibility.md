# Skill Visibility

## Overview

AutoSkillit has 141 bundled skill sources. Session-role skills are organized into three
configurable tiers that control when and where they appear as slash commands. Exact-role
orchestration skills are exposed through role-derived catalogs instead of a user tier. The
tier system is orthogonal to subset categories — you can disable a subset across all tiers
simultaneously or reclassify session-role skills between tiers. See
[Subset Categories](subsets.md) for subset configuration.

## The Three Tiers

### Tier 1 — Free Range (Entry Points)

- **Location**: `src/autoskillit/skills/` (plugin-scanned by Claude Code)
- **Default members**: `open-kitchen`, `close-kitchen`
- **Visible in**: ALL session modes, including plain `$ claude` with the plugin loaded
- `sous-chef` lives in this directory but is an exact-role L2 document, not a Tier 1
  command
- **Filesystem mechanism**: Claude Code auto-discovers skills via `--plugin-dir`; anything
  in `skills/` is registered as `/autoskillit:<name>`

### Tier 2 — Cook (Interactive Skills)

- **Location**: `src/autoskillit/skills_extended/` (NOT plugin-scanned)
- **Default members** (105 total):
  `investigate`, `make-plan`, `implement-worktree`, `rectify`,
  `dry-walkthrough`, `make-groups`, `review-approach`, `mermaid`, `make-arch-diag`,
  `make-experiment-diag`, `plan-visualization`, `select-vis-lenses`, `synthesize-vis-plan`, `phoropter-null-synthesis`, `phoropter-priority-synthesis`,
  all 13 `arch-lens-*` skills, all 18 `exp-lens-*` skills, all 12 `vis-lens-*` skills,
  all 14 `planner-*` skills,
  `audit-arch`, `audit-cohesion`, `audit-tests`,
  `audit-defense-standards`, `audit-bugs`, `audit-friction`, `validate-audit`,
  `audit-docs`, `audit-feature-gates`, `audit-review-decisions`,
  `make-req`, `elaborate-phase`, `write-recipe`, `migrate-recipes`, `setup-project`,
  `design-guards`, `triage-issues`, `collapse-issues`,
  `issue-splitter`, `prepare-issue`, `make-campaign`,
  `scope`, `plan-experiment`, `implement-experiment`, `run-experiment`,
  `generate-report`, `validate-test-audit`, `validate-review-decisions`,
  `stage-data`, `setup-environment`, `bundle-local-report`, `reload-session`
- **Visible in**: cook and headless sessions
- **Mechanism**: projected into a content-keyed plugin artifact. Each physical
  child receives a fresh exact-incarnation binding and inherited reader lease.

### Tier 3 — Pipeline-Only (Automation Skills)

- **Location**: `src/autoskillit/skills_extended/` (same directory as Tier 2)
- **Default members** (32 total):
  `prepare-pr`, `compose-pr`, `open-integration-pr`, `merge-pr`, `analyze-prs`,
  `review-pr`, `resolve-review`, `implement-worktree-no-merge`, `resolve-failures`,
  `retry-worktree`, `resolve-merge-conflicts`, `audit-impl`, `smoke-task`,
  `report-bug`, `pipeline-summary`, `diagnose-ci`, `analyze-pipeline-health`, `verify-diag`,
  `compose-research-pr`, `prepare-research-pr`, `resolve-claims-review`,
  `resolve-design-review`, `resolve-research-review`, `review-research-pr`,
  `audit-claims`, `build-execution-map`, `promote-to-main`,
  `review-design`, `troubleshoot-experiment`,
  `classify-experiment-type`, `apply-review-dimensions`
- **Visible in**: cook and headless sessions
- **Distinction from Tier 2**: semantic only — both tiers live in `skills_extended/` and
  are available in the same session modes. The tier distinction lets users reclassify
  skills between "interactive" and "automation" via config without moving files.

### Role-derived — L2 Orchestrator

- `sous-chef` is the internal L2 operating document injected into order and food-truck
  orchestrators; it is never a user-facing slash command.
- `process-issues` is an L2 command available in role-derived order and food-truck
  catalogs.
- Neither skill appears in `skills.tier1`, `skills.tier2`, or `skills.tier3`.
- L1 session catalogs and L3 fleet catalogs exclude both skills. A custom configuration
  that assigns an exact-role skill to a session tier is invalid.

## Session Mode Skill Visibility

```
Session Mode              Tier 1   Tier 2   Tier 3   L2 role-derived
────────────────────────  ───────  ───────  ───────  ───────────────
$ claude (plugin)           ✓        ✗        ✗             ✗
$ autoskillit cook (L1)     ✓        ✓        ✓             ✗
$ autoskillit order (L2)    ✓        ✓        ✓             ✓
food truck (L2)             ✓        ✓        ✓             ✓
run_skill worker (L1)       ✓        ✓        ✓             ✗
$ autoskillit fleet (L3)    ✓        ✓        ✓             ✗
```

Subset filtering applies after tier and role visibility — a disabled subset removes its
members from the resulting catalog.

## How Skills Are Discovered Per Session Mode

### Regular `$ claude` session

Claude Code loads the plugin via `--plugin-dir <autoskillit-package>/`. It scans
`skills/` and registers `open-kitchen` and `close-kitchen` as `/autoskillit:open-kitchen`
and `/autoskillit:close-kitchen`. Skills in `skills_extended/` are never seen.

### Cook session (`$ autoskillit cook`)

1. AutoSkillit resolves session-role skills from both configured directories,
   applies subset and override rules, and excludes L2-only skills.
2. It validates or publishes a content-keyed projection whose manifest records a
   random, never-reused incarnation identity.
3. Claude Code is launched with that binding's `--plugin-dir` and inherited reader
   descriptor, plus `--add-dir <cwd>` for project-local discovery.
4. Retirement and repair require nonblocking exclusive ownership, so a projection
   retained by any live child remains readable until the final descriptor closes.

### Order session (`$ autoskillit order`)

Order is similar to cook, but its L2 role-derived catalog additionally includes
`process-issues`. The internal `sous-chef` document is injected and the kitchen is
pre-opened.

### Headless session (launched by `run_skill`)

Before launch, `run_skill` resolves the requested L1 skill and its dependency closure
from the fixed-precedence effective catalog. That resolution includes applicable
project-local overrides; role, capability, configured-tier, and closure contracts are
validated before session initialization or filesystem writes.

The selected canonical documents are then projected into an exact, content-keyed
plugin artifact. Machine-only authority (`uses_capabilities`, `execution_role`,
`activate_deps`, and retired `backend_requirements`) stays in AutoSkillit's private
contract and is omitted from every model-facing `SKILL.md`. Claude Code receives the
sanitized projection through the same launch binding whose shared descriptor reaches
the child, rather than a raw `skills_extended/` directory. Exact
ORCHESTRATOR-role entries such as `process-issues` remain excluded from the L1 catalog,
and project-local bytes are exposed only when that source won effective resolution.
The `AUTOSKILLIT_HEADLESS` environment variable activates session-boundary enforcement.

Interactive tool visibility is not proof that Claude has completed its startup
tool-list snapshot. `open_kitchen` keeps its per-tool
`anthropic/alwaysLoad` metadata, while server-level `alwaysLoad` remains
disabled until the measured initial-schema cost is accepted. See
[Claude startup readiness](../execution/claude-startup-readiness.md).

## Troubleshooting Live Inline Projections

`autoskillit@inline` is a session-only projection, not a marketplace installation.
Consequently, `/plugin` reinstall is inapplicable when an active inline session reports
stale or damaged plugin content.

Reprojection or `/reload-plugins` can provide temporary recovery for a new launch, but
neither command repairs artifact lifetime for a child that is already running. Each
physical child owns a binding to one exact incarnation; AutoSkillit defers repair,
retirement, and reclamation while any reader lease for that incarnation remains open.
If a live session is unhealthy, end that session, start a new launch, and retain the
lifecycle logs before treating reprojection as evidence of a durable fix. This is the
operational boundary established for issue #4382.

## Config-Driven Tier Reclassification

Any session-role bundled skill can be promoted or demoted via
`.autoskillit/config.yaml`:

```yaml
# .autoskillit/config.yaml
skills:
  tier1:
    - investigate   # promote to always-visible (appears in plain $ claude session)
  tier2:
    - investigate   # WRONG: do NOT repeat a skill in multiple tiers (validation error)
    - make-plan
  tier3:
    - open-pr
    - merge-pr
```

**Rules:**
- A skill must appear in exactly one tier (listed in multiple tiers = validation error)
- Exact-role skills cannot be assigned to a session tier
- Unknown skill names are logged as a warning, not a crash
- Resolution order: package defaults → user config (`~/.autoskillit/config.yaml`) →
  project config (`.autoskillit/config.yaml`), last wins (dynaconf)

## Tier × Subset Interaction

Disabling a subset removes its members from the ephemeral session directory regardless
of tier. The two axes compose independently:

| | Subset ENABLED | Subset DISABLED |
|---|---|---|
| **Tier 1** | Skill visible in all sessions | Skill hidden from all sessions |
| **Tier 2** | Skill visible in cook + headless | Skill hidden from all sessions |
| **Tier 3** | Skill visible in cook + headless | Skill hidden from all sessions |

See [Subset Categories](subsets.md) for how to configure subset disablement.

## Why Two Directories, Not `disable-model-invocation`

Claude Code's `disable-model-invocation` setting is ignored for plugin-provided skills
(Claude Code issue #22345). The ONLY reliable way to hide extended-tier skills from regular
`$ claude` sessions is to keep them out of the plugin's `skills/` directory. AutoSkillit
uses a two-directory layout (`skills/` for Tier 1, `skills_extended/` for Tiers 2+3)
to enforce this boundary at the filesystem level.
