# server/lifecycle/

Server state, session-type visibility, access guards, and lifespan boot —
relocated from `server/` (issue #4673) with no behavior change.

## Responsibilities

The mutable singleton `_ctx` and its context accessors (`_get_ctx`,
`_get_ctx_or_none`, `_get_config`, `_initialize`, `deferred_initialize`,
`version_info`) live in `_state`. Orchestration-level gate functions that
decide whether a tool call is permitted live in `_guards`. Session-type tag
visibility dispatch — which kitchen-tagged tools are pre-revealed for a
given `AUTOSKILLIT_SESSION_TYPE` — lives in `_session_type`. The
pre-deletion editable-install scan that halts `perform_merge()` before a
worktree is deleted lives in `_editable_guard`. The FastMCP lifespan boot
sequence — the async context manager wired via `lifespan=`, per-session-type
auto-gate boots, and one-shot startup checks — lives in the `_lifespan/`
sub-package.

## Bootstrap order

`server/__init__.py` imports `_lifespan` and `_state` before constructing
`FastMCP`, and imports `_session_type` afterward as a deferred, tool-registration-time
binding. That relative ordering is load-bearing: the lifespan context manager
is passed to `FastMCP(...)` at construction, while `_apply_session_type_visibility`
runs only after every tool module has registered. Preserve both positions
when touching `server/__init__.py`.

## Import boundary

Consumers import from the defining module under `autoskillit.server.lifecycle`,
not through a package-level re-export — this package's `__init__.py` carries
a responsibility docstring only. External production packages continue
using the `autoskillit.server` gateway. Existing package-level patch seams
(module-object attribute lookups used by monkeypatches, such as
`_lifespan_pkg`-style bindings) resolve through the relocated modules
unchanged; only their dotted import path moved.
