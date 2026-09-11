# server/lifecycle/_lifespan/

FastMCP lifespan boot sequence — relocated from `server/_lifespan/`
(issue #4673) with no behavior change.

## Responsibilities

`_startup_checks` runs one-shot synchronous startup checks
(`run_startup_drift_check`, `run_startup_hook_health_check`,
`run_startup_install_state_check`, `run_startup_fix_required_coverage_check`,
`run_startup_join_guard_coverage_check`, `_activate_recipe_kitchen`,
`_retain_context_tracker_authority`, `_finalize_recorder`). `_session_boots`
holds the per-session-type async auto-gate boots and the
`_LIFESPAN_BOOT_REGISTRY` dispatch table that picks exactly one boot path
based on session type and the presence of a sealed launch authority.
`_lifespan` provides the `_autoskillit_lifespan` async context manager wired
into FastMCP via `lifespan=`, plus the async wrappers that offload the
blocking startup checks to executor threads.

## Bootstrap order

The lifespan's pre-yield section writes the readiness sentinel first, then
submits deferred startup work (recovery, audit loading, stale cleanup,
drift check) as background tasks so they run after the transport opens.
`__aexit__` calls `recorder.finalize()` so scenario data survives SIGTERM,
and cleans up the sentinel in `finally:` before that finalize runs.

## Import boundary

Consumers import from the defining module under
`autoskillit.server.lifecycle._lifespan`. The three leaf modules
(`_lifespan.py`, `_session_boots.py`, `_startup_checks.py`) import this
package itself as a runtime module-object lookup seam so existing
monkeypatches keep resolving through the package initializer; that seam is
unchanged by the move except for its dotted path.
