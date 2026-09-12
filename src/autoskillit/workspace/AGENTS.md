# workspace/

IL-1 workspace management — clone lifecycle, worktrees, skill resolution.

## Architecture Notes

**Plugin authorities are derived from `pkg_root()` and bind projections per launch.**
No code here may resolve a plugin root from `installed_plugins.json` or the
Claude Code plugin cache: those are derived copies that a third party versions
and garbage-collects, and reading one produces sessions that run stale
recipes/agents/hooks against current code. `project_default_plugin_authority()`
is lazy; only its launch binding carries a validated artifact path and lease.
The narrow exception for install-state diagnostics is documented in
`_installed/AGENTS.md`.

**Containment checks over write destinations use `destination_location()`, never
`Path.resolve()`** — resolve follows a final-component symlink, which answers
"what does this point at?" instead of "where may I write?".

`session_skills.py` is the stable identity-preserving facade for per-session ephemeral
copies of the bundled skill set so that headless sessions can use a filtered subset
without polluting the installed package. The canonical owners are
`session_skill_catalog.py` (catalog compilation, finalized-role reachability, profile
admission helpers, and the durable unavailability writer), `session_skill_provider.py`
(`SkillsDirectoryProvider`, ephemeral-root discovery, closure write-dir resolution),
`session_skill_lifecycle.py` (lock path, `_SessionLease`, persistent-root resolution,
stateless lease/removal primitives), `session_skill_materialization.py` (the
ordering-sensitive `_materialize_session` transaction, single catalog merge, legacy
discovery alias, layout validation), and `session_skill_manager.py`
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
