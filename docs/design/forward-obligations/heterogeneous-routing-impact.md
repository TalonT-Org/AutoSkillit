# Heterogeneous Per-Item Backend Routing Contract Impact

| Field | Value |
|-------|-------|
| Status | Draft |
| Date | 2026-06-27 |

## Obligation

Heterogeneous per-item backend routing means individual work-items in a fleet campaign can
be dispatched to different backends (e.g., item A to `ClaudeCodeBackend`, item B to
`CodexBackend`) rather than all items sharing a single campaign backend.

## Current State

- `BACKEND_REGISTRY` in `execution/backends/__init__.py:46–49` maps string names to backend
  classes: `{"claude-code": ClaudeCodeBackend, "codex": CodexBackend}`.
- `ToolContext.backend` (`pipeline/context.py:164`) is a single
  `CodingAgentBackend | None` instance — one backend per session.
- Fleet dispatch reads `ctx.backend` once per campaign.
- `dispatch_food_truck()` (`execution/headless/__init__.py:349`) resolves
  `self._ctx.backend` at line 416 — no per-item routing. It raises `RuntimeError` if
  `food_truck_capable` is `False` (line 385).
- `run_headless_core` supports a per-step `backend_override` string (lines 198–199) that
  resolves to a local `step_backend` via `get_backend()`, but this override is call-local
  and does not mutate `ctx.backend`.

## Contract Gaps

1. **`ToolContext.backend` is a single-backend constraint.** Heterogeneous routing requires
   either making `backend` a per-item selection or introducing a routing callable.
2. **`build_food_truck_cmd` and `dispatch_food_truck` assume a single backend.** The entire
   fleet dispatch path reads `ctx.backend` without per-item indirection.
3. **`BackendCapabilities.food_truck_capable`** (`_type_backend.py:80`) is the existing gate.
   Both `ClaudeCodeBackend` (`CLAUDE_CODE_CAPABILITIES:226`) and `CodexBackend`
   (`codex.py:556`) set it to `True`.

## Required CodingAgentBackend Contract Additions

1. Define a backend routing selector type:
   `BackendSelector = Callable[[WorkItem], CodingAgentBackend]`
2. `food_truck_capable` must remain `True` only on backends that satisfy the full dispatch
   contract (command construction, output parsing, session lifecycle).
3. `dispatch_food_truck` must accept a selector or per-item backend resolution rather than
   reading `ctx.backend` unconditionally.

## Key Constraint

`BackendCapabilities` is a frozen dataclass. Adding a routing-selector field would need to
be forward-declared with a tracking issue (added to `_FORWARD_DECLARED` in
`test_capability_consumption.py`) until the fleet routing layer is refactored to consume it.

## Key References

- `execution/backends/__init__.py:46–49` — `BACKEND_REGISTRY`
- `execution/headless/__init__.py:349` — `dispatch_food_truck`
- `pipeline/context.py:164` — `ToolContext.backend`
- `core/types/_type_backend.py:80` — `BackendCapabilities.food_truck_capable`
- `fleet/` — campaign dispatch (reads `ctx.backend` per campaign)
