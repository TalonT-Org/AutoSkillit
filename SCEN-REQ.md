# SCEN-REQ: Bundled Recipe Sync Behavior Contract

**Feature:** Content-aware bundled recipe sync
**Source:** `sync_bundled_recipes()` in `recipe_loader.py`, `open_kitchen` prompt in `server.py`, `autoskillit update` in `cli.py`
**Status:** Implemented. All scenarios and requirements listed here must continue to be satisfied.

This document is the authoritative reference for what the bundled recipe sync subsystem must support. Every requirement listed here has a corresponding test. Do not remove functionality without first removing the corresponding requirement and scenario from this document and getting explicit sign-off.

---

## Group A: Day-to-Day Startup Safety

### SCEN-001: Plugin user wants to start the MCP server without losing local recipe customizations made between sessions

- **REQ-A-001:** When the server-startup sync encounters a local recipe whose content differs from the current bundled version, the local file must not be modified.
- **REQ-A-002:** A local recipe is considered unmodified if its current content is identical to either (a) the current bundled recipe content, or (b) the content hash recorded in the sync manifest for that recipe at the time it was last written by the sync.
- **REQ-A-003:** A local recipe that satisfies the unmodified condition (REQ-A-002) must be overwritten with the current bundled content regardless of whether the bundle has changed since the last sync.

### SCEN-002: Plugin user wants to know when the server startup sync has skipped overwriting a recipe because it was locally modified

- **REQ-A-004:** When the server-startup sync preserves a locally modified recipe, a warning message must be emitted to the server's stderr log. The warning must identify the recipe by name.
- **REQ-A-005:** The warning log message must be at WARNING level or equivalent, not DEBUG or INFO.

### SCEN-003: Plugin user wants unmodified bundled recipes to be kept up to date automatically without any action on their part

- **REQ-A-003:** *(shared)* A local recipe that satisfies the unmodified condition must be overwritten with the current bundled content on every server startup where the bundle content differs.
- **REQ-A-006:** The server-startup sync path must apply the same overwrite eligibility logic used by `autoskillit update` — no separate update command invocation must be required to receive recipe updates.

---

## Group B: Bundle Update Adoption Across Plugin Versions

### SCEN-004: Plugin user wants the server to recognize that a locally unmodified recipe (which happens to match an older bundle version) should be upgraded to the current bundle version automatically

- **REQ-A-002:** *(shared)* A local recipe is considered unmodified if its current content hash matches the hash stored in the sync manifest.
- **REQ-C-001:** *(shared)* A sync manifest file, persisted under `.autoskillit/`, records the content hash of each bundled recipe at the time it was last written by the sync.
- **REQ-C-002:** *(shared)* After each successful sync write, the sync manifest entry for that recipe must be updated to the hash of the content just written.
- **REQ-C-003:** *(shared)* The sync manifest must survive MCP server restarts (it must be file-backed, not in-memory).

### SCEN-005: Plugin user wants to be notified when a bundle update arrives for a recipe they have locally modified, so they can decide whether to adopt it

- **REQ-B-001:** When `open_kitchen` is activated and at least one bundled recipe has a newer version available and that recipe is locally modified (local hash does not match the sync manifest hash), the `open_kitchen` prompt message must include an advisory listing the affected recipe names.
- **REQ-B-002:** The advisory must be phrased to invite a user decision — it must not state that the recipe has been updated, only that an update is available and the user's local version differs.
- **REQ-B-003:** The advisory must not appear when there are no pending updates for modified recipes.

### SCEN-006: Plugin user wants to be able to decline a bundle update for a specific recipe and not be asked again for that same update

- **REQ-D-001:** *(shared)* A file-backed sync decision store, persisted under `.autoskillit/`, records per-recipe user decisions (accept or decline) keyed by both recipe name and the bundled content hash at the time of the decision.
- **REQ-D-002:** When a user declines a bundle update for recipe X at bundled hash H, subsequent `open_kitchen` activations must not include recipe X in the advisory until the bundled content for recipe X changes.
- **REQ-D-003:** *(shared)* The sync decision store must survive MCP server restarts.

### SCEN-007: Plugin user wants to be re-prompted to adopt a bundled recipe update if the bundle advances to a newer version after they previously declined

- **REQ-D-001:** *(shared)* The sync decision store keys decisions by (recipe-name, bundled-content-hash) pair — not by recipe-name alone.
- **REQ-D-004:** When the bundled content hash for recipe X advances beyond the hash at which the user previously declined (H → H'), the recipe must be re-eligible for inclusion in the `open_kitchen` advisory.
- **REQ-D-002:** *(shared)* A declination is only suppressed when the current bundled hash exactly matches the hash recorded in the declination entry.

---

## Group C: Sync Manifest (Shared Infrastructure)

### SCEN-012: Plugin user wants decisions about recipe sync to persist across MCP server restarts

- **REQ-C-001:** A sync manifest file, persisted under `.autoskillit/`, records the content hash of each bundled recipe at the time it was last written by the sync.
- **REQ-C-002:** After each successful sync write, the sync manifest entry for that recipe must be updated to reflect the content hash just written.
- **REQ-C-003:** The sync manifest must be file-backed and readable by the sync process on the next server startup.
- **REQ-C-004:** The sync manifest must not be stored under `.autoskillit/temp/` (which is gitignored). It must live directly under `.autoskillit/` so it is committed alongside the project's other AutoSkillit config.

---

## Group D: User Decision Persistence (Shared Infrastructure)

- **REQ-D-001:** A file-backed sync decision store, persisted under `.autoskillit/`, records per-recipe user decisions (accept or decline) keyed by (recipe-name, bundled-content-hash).
- **REQ-D-002:** A stored declination suppresses the `open_kitchen` advisory for recipe X only when the current bundled hash for X matches the recorded declination hash exactly.
- **REQ-D-003:** The sync decision store must survive MCP server restarts.
- **REQ-D-005:** When a user accepts a bundle update for a modified recipe, the sync must immediately overwrite the local recipe with the bundled version and update the sync manifest with the new hash.

---

## Group E: Transparency and Permanent Opt-Out

### SCEN-009: Plugin user wants to understand which recipes are unmodified, customized, or have pending bundle updates

- **REQ-E-001:** At `open_kitchen` activation, the advisory message must accurately reflect the current sync state for all bundled recipes.
- **REQ-E-002:** The `autoskillit doctor` command must include a recipe sync-status check that reports, for each bundled recipe: whether the local copy is unmodified, locally modified, or has a pending bundle update available.

### SCEN-010: Plugin user wants to opt a specific recipe out of all future bundled sync updates permanently

- **REQ-E-003:** The project config (`.autoskillit/config.yaml`) must support a `sync.excluded_recipes` list of recipe names permanently excluded from the server-startup sync.
- **REQ-E-004:** Recipes in the exclusion list must never be overwritten by the sync, regardless of whether the local content matches the bundled content.
- **REQ-E-005:** Recipes in the exclusion list must not appear in the `open_kitchen` update advisory.

---

## Group F: Data Safety Guarantee

### SCEN-011: Plugin user wants the sync process to never silently destroy content they wrote

- **REQ-F-001:** The server-startup sync must not overwrite a local recipe unless at least one of the following holds:
  - (a) The local file's content is identical to the current bundled content (no effective change).
  - (b) The local file's content hash matches the hash recorded in the sync manifest for that recipe (content is unmodified since last sync).
  - (c) The user has explicitly accepted the update via the `open_kitchen` advisory in the current or a prior session (REQ-D-005).
- **REQ-F-002:** In no case may the sync overwrite a local recipe that is listed in the permanent exclusion config (REQ-E-003).
- **REQ-F-003:** The sync must not create or remove recipe files other than writing current bundled content to eligible local files. Project-specific recipes (no bundled counterpart) must never be touched.

---

## Shared Requirements Cross-Reference

| Req ID | Summary | Groups |
|--------|---------|--------|
| REQ-A-002 | Unmodified = content matches current bundle OR sync manifest hash | A, B |
| REQ-A-003 | Unmodified local recipes are always overwritten with current bundle | A |
| REQ-C-001 | Sync manifest stores per-recipe last-written content hash | B, C |
| REQ-C-002 | Sync manifest updated after each successful write | B, C |
| REQ-C-003 | Sync manifest is file-backed, survives restarts | B, C |
| REQ-D-001 | Decision store keyed by (recipe-name, bundled-hash) | B, D |
| REQ-D-002 | Declination suppresses advisory only for exact hash match | B, D |
| REQ-D-003 | Decision store is file-backed, survives restarts | B, D |
