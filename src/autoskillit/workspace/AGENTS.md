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

`session_skills/` (issue #4989; a package, not a flat file, since #4989's decomposition
pushed `workspace/`'s top-level file count past its tier limit) is the stable
identity-preserving facade for per-session ephemeral copies of the bundled skill set
so that headless sessions can use a filtered subset without polluting the installed
package. `session_skills/__init__.py` is that facade. It is named `session_skills`,
not `_session_skills`, deliberately: dozens of external call sites already import
`autoskillit.workspace.session_skills` directly (not only through the outer
`autoskillit.workspace` re-export), so the package boundary itself is the long-standing
public contract, exactly like `workspace.skills` or `workspace.clone` — only the shards
*inside* it are private. The canonical owners are `_catalog.py` (catalog compilation,
finalized-role reachability, profile admission helpers, and the durable unavailability
writer), `_provider.py` (`SkillsDirectoryProvider`, ephemeral-root discovery, closure
write-dir resolution), `_lifecycle.py` (lock path, `_SessionLease`, persistent-root
resolution, stateless lease/removal primitives), `_materialization.py` (the
ordering-sensitive `_materialize_session` transaction, single catalog merge, legacy
discovery alias, layout validation), and `_manager.py` (`DefaultSessionSkillManager`,
`_InitializedSession`, and `_materialize_bound_records`). Shards import each other
directly via absolute dotted paths (`autoskillit.workspace.session_skills._catalog`,
etc.) and must never import the package's own `session_skills/__init__.py` facade at
runtime; `TYPE_CHECKING`-guarded imports are exempt. `_projection.py` (formerly the
workspace-root `skill_projection.py`) relocated into the same package as a distinct
*gateway* shard: it owns a small local surface and re-exports the rest of its
`__all__`, identity-equal, from `_projected_artifact`. Only `_provider.py`,
`_materialization.py`, and `_manager.py` may import `_projection.py` — a private-sibling narrowing that
replaces the pre-#4989 cross-subsystem-facade framing without loosening the fan-in
restriction itself. Each shard *and* both facades (`session_skills/__init__.py` and
`_projected_artifact/materialization.py`) are capped at 750 lines
(`tests/arch/test_session_skills_projected_artifact_size_ceilings.py`); split further
rather than growing past it.

`skill_capabilities.py` owns a process-local, weighted LRU keyed by exact canonical
content and normalized logical skill name. The cache bounds resident entries and
accounted payload bytes while coordinating concurrent scans outside its lock.
