# Versioning Pipeline

## Canonical Source

`pyproject.toml` `[project].version` is the single source of truth for the package version.

## Version Propagation

`scripts/sync_versions.py` reads the canonical version from `pyproject.toml` via `tomllib`, then:

- Atomically updates `src/autoskillit/.claude-plugin/plugin.json` `"version"` field
- Regex-replaces `autoskillit_version:` lines in `src/autoskillit/recipes/**/*.yaml`

Pre-commit enforcement: `sync_versions.py --check` (exits 1 if any artifact is out of sync, without modifying files).

## CI Workflows

### `patch-bump-develop.yml`

- **Trigger:** any PR merged into `develop`
- **Action:** `MAJOR.MINOR.(PATCH+1)` on `develop`
- **Calls `sync_versions.py`:** yes
- **Staged files:** `pyproject.toml`, `src/autoskillit/.claude-plugin/plugin.json`, `src/autoskillit/recipes/`, `uv.lock`
- **Concurrency:** serialized (`cancel-in-progress: false`)

### `version-bump.yml`

- **Trigger:** PR merged into `main` from `develop` (promotion)
- **Action:**
  - `main` → `MAJOR.(MINOR+1).0`
  - `develop` → `MAJOR.(MINOR+1).1` (forward-bumped to stay ahead of main)
- **Calls `sync_versions.py`:** yes (on both `main` and `develop` in sequence)
- **Guard:** rejects if `new_develop <= old_develop` (downgrade protection)

### `release.yml`

- **Trigger:** PR merged into `stable`
- **Action:** `MAJOR.(MINOR+1).0` on `stable`, annotated git tag `vX.Y.Z`, GitHub Release
- **Calls `sync_versions.py`:** yes — `plugin.json` and recipe YAMLs are synced (see fix below)
- **Staged files:** `pyproject.toml`, `src/autoskillit/.claude-plugin/plugin.json`, `src/autoskillit/recipes/`, `uv.lock`

## Runtime Health

`version.py:version_info(plugin_dir)` — LRU-cached, reads:

- `importlib.metadata.version("autoskillit")` → `package_version`
- `plugin.json` → `plugin_json_version`
- `recipes/*.yaml` `autoskillit_version:` lines → `stale_recipes`

Returns `match` (`package_version == plugin_json_version`) and `recipe_versions_match` (True if no stale recipes found).

## Known Gap: `release.yml` Did Not Sync Recipes

`patch-bump-develop.yml` and `version-bump.yml` both call `sync_versions.py`, keeping `plugin.json` and recipe YAMLs in sync. Previously, `release.yml` updated `plugin.json` inline but skipped `sync_versions.py`, so recipe YAML `autoskillit_version` fields were not updated on `stable`. This gap has been closed: `release.yml` now calls `python3 scripts/sync_versions.py` and stages `src/autoskillit/recipes/` in the commit step, consistent with the other two workflows.

## Invariant for PRs

Never include manual version bump commits in PRs. CI handles all bumps automatically on merge. Including a manual bump causes a conflict with the CI-committed bump and must be reverted.
