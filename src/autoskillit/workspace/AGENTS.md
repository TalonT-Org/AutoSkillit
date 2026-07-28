# workspace/

IL-1 workspace management — clone lifecycle, worktrees, skill resolution.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `DefaultCloneManager`, `SkillResolver`, `DefaultSessionSkillManager` |
| `cleanup.py` | `CleanupResult`, preserve list |
| `clone.py` | `clone_repo` + `push_to_remote` + `DefaultCloneManager` |
| `_clone_detect.py` | `detect_*` helpers + `RUNS_DIR` + `classify_remote_url` |
| `_clone_remote.py` | `CloneSourceResolution` + probe/isolate remotes |
| `session_skills.py` | Per-session ephemeral skill dirs; subset filtering |
| `skill_capabilities.py` | Semantic classification, bounded process-local evidence memoization, and capability validation |
| `skill_format.py` | SKILL.md frontmatter validation per agentskills.io spec |
| `skill_projection.py` | Agent-safe projections of typed skill machine contracts |
| `_projected_artifact/` | Projected plugin publication, validation, and launch-lease lifecycle |
| `_projection_cache.py` | Projection asset inventory, cache-key record, and orphan sweep |
| `_install_state.py` | `verify_install_state()` + retired-artifact-shape reconciler |
| `clone_registry.py` | Shared file-based coordination for deferred cleanup |
| `skills.py` | `SkillResolver` — bundled skill listing |
| `worktree.py` | Git worktree creation and teardown helpers |

## Architecture Notes

**Plugin authorities are derived from `pkg_root()` and bind projections per launch.**
No code here may resolve a plugin root from `installed_plugins.json` or the
Claude Code plugin cache: those are derived copies that a third party versions
and garbage-collects, and reading one produces sessions that run stale
recipes/agents/hooks against current code. `project_default_plugin_authority()`
is lazy; only its launch binding carries a validated artifact path and lease.

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
