# workspace/

IL-1 workspace management — clone lifecycle, worktrees, skill resolution.

## Architecture Notes

**Plugin authorities are derived from `pkg_root()` and bind projections per launch.**
No code here may resolve a plugin root from `installed_plugins.json` or the
Claude Code plugin cache: those are derived copies that a third party versions
and garbage-collects, and reading one produces sessions that run stale
recipes/agents/hooks against current code. `project_default_plugin_authority()`
is lazy; only its launch binding carries a validated artifact path and lease.
The narrow exception is install-state diagnostics in `_install_state.py` and
`_installed_artifact.py`: registry entries are read only as evidence that the
trusted home/plugin/version-derived artifact is obligatory. They are never
used as path authority, and exact identity is validated under its stable
sidecar lease.

**Containment checks over write destinations use `destination_location()`, never
`Path.resolve()`** — resolve follows a final-component symlink, which answers
"what does this point at?" instead of "where may I write?".

**Changing the shape of an artifact we write under `~/` requires an entry in
`RETIRED_INSTALL_ARTIFACT_SHAPES`** (`core/types/_type_constants_retirements.py`), consumed
at runtime by `reconcile_install_artifacts()` here. `~/.autoskillit/` outlives
years of releases, so a shape change with no registry entry strands every
pre-existing install.

Clone paths live under `RUNS_DIR` (resolved by `_clone_detect.py`). `clone_registry.py`
coordinates deferred cleanup across concurrent pipeline sessions using file-based locking.
`session_skills.py` is the stable identity-preserving facade for per-session ephemeral
copies of the bundled skill set so that headless sessions can use a filtered subset
without polluting the installed package. The canonical owners are
`session_skill_catalog.py` (catalog compilation, finalized-role reachability, profile
admission helpers, and the durable unavailability writer), `session_skill_provider.py`
(`SkillsDirectoryProvider`, ephemeral-root discovery, closure write-dir resolution),
`session_skill_lifecycle.py` (lock path, `_SessionLease`, persistent-root resolution,
stateless lease/removal primitives), `session_skill_materialization.py` (the
ordering-sensitive `_materialize_session` transaction, profile projection, persistent
discovery links, layout validation), and `session_skill_manager.py`
(`DefaultSessionSkillManager`, `_InitializedSession`, and `_materialize_bound_records`).
Shards import each other directly and must never import the `session_skills.py`
facade at runtime; `TYPE_CHECKING`-guarded imports are exempt, and
`session_skill_provider.py` and `session_skill_materialization.py` may import the
cross-subsystem `skill_projection` facade. The shards deliberately sit flat in
`workspace/` rather than under a private `_session_skills/` subpackage — the
`test_no_external_module_imports_session_skill_shards_directly` AST guard in
`tests/arch/test_session_skills_projected_artifact_one_way_imports.py` enforces
the same one-way rule that the leading underscore enforces for
`_projected_artifact/`, so a flat layout buys no enforcement gap and a
subpackage move would force path-string churn in the fcntl/mutation
allowlists (see `tests/_retention_surface.py`,
`tests/infra/test_plugin_source_ratchets.py`). Each shard *and* both facades are
capped at 750 lines
(`tests/arch/test_session_skills_projected_artifact_size_ceilings.py`); split further
rather than growing past it.

`skill_capabilities.py` owns a process-local, weighted LRU keyed by exact canonical
content and normalized logical skill name. The cache bounds resident entries and
accounted payload bytes while coordinating concurrent scans outside its lock.

**`_shared_asset_store.py`** hardlinks the verbatim, byte-identical plugin assets
(`assets/`, `hooks/`, `recipes/`, `agents/`) that every projection of the same release
shares, instead of each projection carrying its own copy. Design constraints, all
load-bearing:

- **Store root outside `projections_root`.** `resolve_shared_asset_store_root()` never
  places the store under the projections root `prune_stale_projections`
  (`_projection_cache.py`) enumerates — commit `0949f8a8f` (#4689/#4690) already fixed
  exactly this mistake once (a plugin-generations store misidentified as a stale
  projection).
- **Same device, or no store at all.** The candidate root is resolved from
  `tempfile.gettempdir()` and its `st_dev` must equal `projections_root`'s `st_dev`
  (`os.link()` raises `EXDEV` across filesystems). A mismatch returns `None` — callers
  fall back to `copy2` wholesale for every file, never attempt-and-catch `EXDEV`
  per file.
- **Hardlink with `copy2` fallback, never symlinks.** `link_or_copy_asset()` calls
  `os.link()` and falls back to `shutil.copy2()` on any `OSError` (cross-device, no
  hardlink support, or the store being unavailable). `os.symlink` is never used —
  the shared store must not become a way around the existing symlink prohibition.
- **Bounded populate lease.** `_populate_store_entry()` acquires a timeboxed
  `ArtifactLease` (`_STORE_LEASE_TIMEOUT_SECONDS`) before writing a new store entry,
  defending against the #4511 store-wide capacity exhaustion pattern (an
  un-timeboxed flock on a hot path under concurrent xdist × worktree load). Lease
  contention or failure falls through to `copy2`, never blocks indefinitely.
