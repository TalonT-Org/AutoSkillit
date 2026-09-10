# server/response/_response_budget/

Lossless, shape-preserving response budget enforcement for MCP handlers —
relocated from `server/_response_budget/` (issue #4673) with no behavior
change.

## Responsibilities

`_enforce` owns `enforce_response_budget` and the checkpoint-segmented
response-shaping loop. `_primitives` owns the budget-failure cause
registry, the spill-metadata schema constants, and the shared
serialization/token-estimation helpers. `_projection` owns projecting an
oversized JSON object down to a delivery-bound-conformant shape.
`_spill` owns the bounded-failure envelope and the on-disk artifact-path
machinery for spillover content that does not fit the projection.

## Import boundary

Consumers import from the defining module under
`autoskillit.server.response._response_budget`, or through the package
facade (`__init__.py`) where a re-export already exists — the facade also
re-exports `RecipeSegmentDeliveryError` and
`build_post_effect_segment_failure` from
`autoskillit.server.recipe._recipe_segment_delivery` for monkeypatch
reachability. `_enforce.py` and `_primitives.py` resolve `logger`,
`atomic_write`, and several other package-level symbols through a
module-object binding (`from autoskillit.server.response import
_response_budget as _response_budget_pkg`) rather than importing them by
name, so tests can patch them via the package facade; that seam is
unchanged by the move except for its dotted import path.
