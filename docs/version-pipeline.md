# Versioning Pipeline

## Canonical Source

`pyproject.toml` `[project].version` is the single source of truth for the package version.

## Version Propagation

`scripts/sync_versions.py` reads the canonical version from `pyproject.toml` via `tomllib`, then
atomically updates `src/autoskillit/.claude-plugin/plugin.json` `"version"` field.

Pre-commit enforcement: `sync_versions.py --check` (exits 1 if any artifact is out of sync, without modifying files).

## CI Workflows

### `patch-bump-develop.yml`

- **Trigger:** any PR merged into `develop`
- **Action:** `MAJOR.MINOR.(PATCH+1)` on `develop`
- **Calls `sync_versions.py`:** yes
- **Staged files:** `pyproject.toml`, `src/autoskillit/.claude-plugin/plugin.json`, `uv.lock`
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
- **Calls `sync_versions.py`:** yes — `plugin.json` is synced
- **Staged files:** `pyproject.toml`, `src/autoskillit/.claude-plugin/plugin.json`, `uv.lock`

## Runtime Health

`version.py:version_info(plugin_dir)` — LRU-cached, reads:

- `importlib.metadata.version("autoskillit")` → `package_version`
- `plugin.json` → `plugin_json_version`

Returns `match` (`package_version == plugin_json_version`).

## Invariant for PRs

Never include manual version bump commits in PRs. CI handles all bumps automatically on merge. Including a manual bump causes a conflict with the CI-committed bump and must be reverted.
