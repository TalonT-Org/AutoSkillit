# Skill Visibility

## Overview

AutoSkillit's 123 bundled skills are organized into three tiers that control when and where
they appear as slash commands. The tier system is orthogonal to subset categories — you can
disable a subset across all tiers simultaneously, or reclassify individual skills between
tiers. See [Subset Categories](subsets.md) for subset configuration.

## The Three Tiers

### Tier 1 — Free Range (Entry Points)

- **Location**: `src/autoskillit/skills/` (plugin-scanned by Claude Code)
- **Default members**: `open-kitchen`, `close-kitchen`
- **Visible in**: ALL session modes, including plain `$ claude` with the plugin loaded
- `sous-chef` lives in this directory but is internal — injected by `open_kitchen` at
  runtime and excluded from user-facing slash commands
- **Filesystem mechanism**: Claude Code auto-discovers skills via `--plugin-dir`; anything
  in `skills/` is registered as `/autoskillit:<name>`

### Tier 2 — Cook (Interactive Skills)

- **Location**: `src/autoskillit/skills_extended/` (NOT plugin-scanned)
- **Default members** (41 total):
  `investigate`, `make-plan`, `implement-worktree`, `rectify`,
  `dry-walkthrough`, `make-groups`, `review-approach`, `mermaid`, `make-arch-diag`,
  all 13 `arch-lens-*` skills, `audit-arch`, `audit-cohesion`, `audit-tests`,
  `audit-defense-standards`, `audit-bugs`, `audit-friction`, `make-req`,
  `elaborate-phase`, `write-recipe`, `migrate-recipes`, `setup-project`,
  `design-guards`, `triage-issues`, `collapse-issues`,
  `issue-splitter`, `enrich-issues`, `prepare-issue`, `process-issues`
- **Visible in**: cook and headless sessions
- **Mechanism**: copied to an ephemeral session directory (cook) or exposed via
  `--add-dir` (headless sessions launched by `run_skill`)

### Tier 3 — Pipeline-Only (Automation Skills)

- **Location**: `src/autoskillit/skills_extended/` (same directory as Tier 2)
- **Default members** (16 total):
  `open-pr`, `open-integration-pr`, `merge-pr`, `analyze-prs`,
  `review-pr`, `resolve-review`, `implement-worktree-no-merge`, `resolve-failures`,
  `retry-worktree`, `resolve-merge-conflicts`, `audit-impl`, `smoke-task`,
  `report-bug`, `pipeline-summary`, `diagnose-ci`, `verify-diag`
- **Visible in**: cook and headless sessions
- **Distinction from Tier 2**: semantic only — both tiers live in `skills_extended/` and
  are available in the same session modes. The tier distinction lets users reclassify
  skills between "interactive" and "automation" via config without moving files.

## Session Mode Skill Visibility

```
Session Mode           Tier 1   Tier 2   Tier 3
─────────────────────  ───────  ───────  ───────
$ claude (plugin)        ✓        ✗        ✗
$ autoskillit cook       ✓        ✓        ✓
$ autoskillit order      ✓        ✓        ✓
run_skill (headless)     ✓        ✓        ✓
```

Note: All modes see Tier 1. Cook, order, and headless sessions see Tiers 2 and 3.
Subset filtering applies after tier visibility — a disabled subset removes its members
from all tiers.

## How Skills Are Discovered Per Session Mode

### Regular `$ claude` session

Claude Code loads the plugin via `--plugin-dir <autoskillit-package>/`. It scans
`skills/` and registers `open-kitchen` and `close-kitchen` as `/autoskillit:open-kitchen`
and `/autoskillit:close-kitchen`. Skills in `skills_extended/` are never seen.

### Cook session (`$ autoskillit cook`)

1. AutoSkillit creates an ephemeral session directory at `/dev/shm/autoskillit-sessions/<id>/`
2. Skills from both `skills/` and `skills_extended/` are copied into this ephemeral dir
   (subset-filtered and override-aware)
3. Claude Code is launched with `--plugin-dir <ephemeral-dir>` and `--add-dir <cwd>` so
   project-local skills in `.claude/skills/` are also discoverable
4. All 60 bundled slash-command skills appear as `/autoskillit:*` slash commands within the session
5. The ephemeral directory is cleaned up when the session ends

### Order session (`$ autoskillit order`)

Order is similar to cook: AutoSkillit launches Claude Code with access to all tiers.
The key difference is the orchestrator (`sous-chef` skill) is injected and the kitchen
is pre-opened so all 48 MCP tools are available from the start.

### Headless session (launched by `run_skill`)

`run_skill` launches a headless Claude Code process with:
```
claude --add-dir <skills_extended/> --add-dir <cwd>
```
Both `skills_extended/` skills and project-local skills in `.claude/skills/` are
discoverable. Tier 1 skills from `skills/` are available via the installed plugin.
The AUTOSKILLIT_HEADLESS environment variable activates session-boundary enforcement.

## Config-Driven Tier Reclassification

Any bundled skill can be promoted or demoted via `.autoskillit/config.yaml`:

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
