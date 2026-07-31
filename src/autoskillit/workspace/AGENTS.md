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
`RETIRED_INSTALL_ARTIFACT_SHAPES`** (`core/types/_type_constants.py`), consumed
at runtime by `reconcile_install_artifacts()` here. `~/.autoskillit/` outlives
years of releases, so a shape change with no registry entry strands every
pre-existing install.

Clone paths live under `RUNS_DIR` (resolved by `_clone_detect.py`). `clone_registry.py`
coordinates deferred cleanup across concurrent pipeline sessions using file-based locking.
`session_skills.py` builds per-session ephemeral copies of the bundled skill set so that
headless sessions can use a filtered subset without polluting the installed package.

`skill_capabilities.py` owns a process-local, weighted LRU keyed by exact canonical
content and normalized logical skill name. The cache bounds resident entries and
accounted payload bytes while coordinating concurrent scans outside its lock.
