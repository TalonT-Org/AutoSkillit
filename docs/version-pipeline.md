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
- **Concurrency:** cancel-in-progress (`cancel-in-progress: true`) — safe because each run independently reads the current version

### `version-bump.yml`

- **Trigger:** PR merged into `main` from `develop` (promotion)
- **Action:**
  - `main` → `MAJOR.(MINOR+1).0`
  - `develop` → `MAJOR.(MINOR+1).1` (forward-bumped to stay ahead of main)
- **Calls `sync_versions.py`:** yes (on both `main` and `develop` in sequence)
- **Guard:** rejects if `new_develop <= old_develop` (downgrade protection)
- **Concurrency:** serialized (`cancel-in-progress: false`) — must complete both main and develop pushes atomically

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

### Immutable version-addressed install roots (Phase 3, issue #4597)

AutoSkillit's own Python package install lives under a generation-store tree
disjoint from the pre-existing plugin-artifact generation store:

    ~/.autoskillit/plugin-generations/autoskillit-install/{version}/{incarnation_id}/

Each version directory has a `current` symlink selecting its active
incarnation, and the store root has a version-independent `current` symlink
one level up pointing at whichever `{version}/{incarnation_id}` is live
overall (`generation_store_root` / `generation_version_root` /
`generation_selector_path` / `generation_plugin_selector_path` in
`core/_plugin_artifact_identity.py` — shared path-shape functions also used by
the pre-existing plugin generation store). `publish_install_root_generation()`
(`workspace/_projected_artifact/_generation_publication.py`) finalizes a
generation whose content an installer already wrote directly at its final,
version-keyed destination: digest, a lease, a manifest, digest
re-verification, the atomic selector flip (the sole commit point), and
enqueueing every previously selected generation for retirement.

Because `uv`/`venv` console scripts bake an absolute shebang path at creation
time (a venv is not relocatable — moving it after the fact breaks every
generated entry point), the update transaction cannot install once and rename
into place the way the plugin-content generation store does. Instead
`cli/update/_transaction.py`'s `INSTALL_ROOT_GENERATION_PUBLICATION` phase
runs a two-install sequence, right after the post-pivot fresh-version-metadata
gate resolves the new version and before the plugin's own install-child
invocation: a disposable *probe* install (`UV_TOOL_DIR` pointed at
`core.generation_staging_root()`) exists purely to discover the version, then
a second, near-free install — `uv`'s local cache makes a repeat install of the
same already-resolved commit a cache hit — writes the real content directly
at its permanent `generation_artifact_root()` destination and is never moved
again.

An exec-time entrypoint shim at `~/.local/bin/autoskillit`, rendered by
`core/_entrypoint_shim.py`, replaces `uv`'s own generated console-script
wrapper. It resolves the install-root generation selector exactly once at
exec time, then `os.execv`s straight into the resolved incarnation's own
entrypoint (`{generation}/autoskillit/bin/autoskillit`) — never re-consulting
the selector afterward. This is what makes an already-started process immune
to a concurrent upgrade: the selector flip that publishes a new generation
cannot affect a process that already completed its one-time resolution and
handed control to the kernel's `execve`.

Superseded install-root generations are reclaimed by lease, not by force: they
are enqueued into the existing retirement engine
(`PluginArtifactRetirementEngine` / `GenerationArtifactRetirementOwner`, via
the `PluginArtifactKind.INSTALL_ROOT_GENERATION` routing kind) with the
standard 24-hour grace window (`_GENERATION_GRACE` in
`_generation_publication.py`), and are only actually removed once grace has
elapsed **and** an exclusive lease is acquirable **and** the generation is not
the current selector. Long-lived processes additionally self-acquire and hold
a shared lease on their own install-root generation at first access
(`_acquire_self_lease()` in `core/_install_binding.py`, called from
`resolve_install_binding()`) for the process's entire lifetime — this durably
blocks reclaim of a root a live process is still reading from, independent of
the grace window or how many later versions have superseded it.

The old "restart the process" remedy is gone. `assert_generator_process_fresh()`
(`workspace/_projected_artifact/authority.py`) and the content-hash staleness
detector (`recipe/_api_cache.py`) both remain as defensive backstops for
installs shaped outside this lifecycle (dev/editable checkouts, external
tampering) — they no longer fire on AutoSkillit's own upgrades, because those
upgrades never mutate or delete a root a live process is reading from.

**Migrating off the pre-Phase-3 shared uv tool root.** Before Phase 3, every
install lived at one shared, non-versioned path
(`~/.local/share/uv/tools/autoskillit/`), which had no manifest or lease
infrastructure at all. `reconcile_install_artifacts()`
(`workspace/_install_state.py`) preserves that legacy tree because it has no
lease or exact-identity metadata from which AutoSkillit can prove that no live
process still uses it. Retiring the shared root therefore requires explicit
operator cleanup after its processes have stopped; automated reconciliation
only reports the preserved legacy shape.

**Reducing prompt frequency is a separate, operational concern from surviving
an upgrade.** Everything above prevents a concurrent upgrade from destroying
a live process's work, on any track. It does not change how often `develop`
finds a newer version to prompt about — see
[the pinning recommendation](update-checks.md#branch-aware-dismissal-windows)
in `update-checks.md` for reducing that frequency by tracking a release tag
instead.

## Invariant for PRs

Never include manual version bump commits in PRs. CI handles all bumps automatically on merge. Including a manual bump causes a conflict with the CI-committed bump and must be reverted.
