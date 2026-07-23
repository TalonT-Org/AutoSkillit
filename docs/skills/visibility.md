# Skill Visibility

## Overview

AutoSkillit has 142 bundled skill sources. Session-role skills are organized into three
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
  `issue-splitter`, `enrich-issues`, `prepare-issue`, `make-campaign`,
  `scope`, `plan-experiment`, `implement-experiment`, `run-experiment`,
  `generate-report`, `validate-test-audit`, `validate-review-decisions`,
  `stage-data`, `setup-environment`, `bundle-local-report`, `reload-session`
- **Visible in**: cook and headless sessions
- **Mechanism**: copied to an ephemeral session directory (cook) or exposed via
  `--add-dir` (headless sessions launched by `run_skill`)

### Tier 3 — Pipeline-Only (Automation Skills)

- **Location**: `src/autoskillit/skills_extended/` (same directory as Tier 2)
- **Default members** (31 total):
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

1. AutoSkillit creates an ephemeral session directory at `/dev/shm/autoskillit-sessions/<id>/`
2. Session-role skills from both configured directories are copied into this ephemeral
   dir (subset-filtered and override-aware); L2-only skills are excluded
3. Claude Code is launched with `--plugin-dir <ephemeral-dir>` and `--add-dir <cwd>` so
   project-local skills in `.claude/skills/` are also discoverable
4. The ephemeral directory is cleaned up when the session ends

### Order session (`$ autoskillit order`)

Order is similar to cook, but its L2 role-derived catalog additionally includes
`process-issues`. The internal `sous-chef` document is injected and the kitchen is
pre-opened.

### Headless session (launched by `run_skill`)

`run_skill` launches a headless Claude Code process with:
```
claude --add-dir <skills_extended/> --add-dir <cwd>
```
The worker receives the resolved L1 catalog plus applicable project-local skills.
Exact ORCHESTRATOR-role entries such as `process-issues` are excluded. The AUTOSKILLIT_HEADLESS
environment variable activates session-boundary enforcement.

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
