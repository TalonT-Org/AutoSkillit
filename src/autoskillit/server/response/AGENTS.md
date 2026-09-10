# server/response/

Response conformance, run_skill completion, and response-budget
enforcement — relocated from `server/` (issue #4673) with no behavior
change.

## Responsibilities

`_response_conformance` provides `ResponseConformanceMiddleware` and
`decide_response_conformance`, the post-conversion conformance checks that
validate registered string-tool output against its declared schema.
`_run_skill_completion` provides `RunSkillCompletionMiddleware` and the
admission/exact-delivery boundary for `run_skill` receipts
(`FinalizedRunSkillCompletionResponse`). The `_response_budget/` sub-package
owns lossless, shape-preserving response budget enforcement
(`enforce_response_budget`) plus the spill envelope and projection
machinery it delegates to.

## Middleware order

`server/__init__.py` registers `RunSkillCompletionMiddleware`,
`ClaudeCodeCompatMiddleware`, and `ResponseConformanceMiddleware` in that
order, after tool module imports and visibility-tag setup. FastMCP
prepends its own `DereferenceRefsMiddleware`, so `mcp.middleware` holds
four entries at runtime; tests assert the three registered names by
relative position, not exact-list equality. Preserve both the registration
order and its position after tool imports when touching
`server/__init__.py`.

## Import boundary

Consumers import from the defining module under
`autoskillit.server.response`, not through a package-level re-export —
this package's `__init__.py` carries a responsibility docstring only.
External production packages continue using the `autoskillit.server`
gateway. Existing package-level patch seams (module-object attribute
lookups used by monkeypatches, such as the `_response_budget_pkg`-style
bindings in `_response_budget/_enforce.py` and `_primitives.py`) resolve
through the relocated modules unchanged; only their dotted import path
moved.
