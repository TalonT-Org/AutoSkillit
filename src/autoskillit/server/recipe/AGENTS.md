# server/recipe/

Recipe artifact, delivery, execution, generation, and initialization —
relocated from `server/` (issue #4673) with no behavior change.

## Responsibilities

Compiled-recipe generation and its persisted artifacts are owned by
`_recipe_generation` and `_recipe_artifact` respectively. Transactional
finalization lives in the `_recipe_delivery/` sub-package, with its own
decision-support helpers in `_recipe_delivery_helpers` and its segmented
startup/checkpoint carriers in `_recipe_segment_delivery`. Server-owned
execution and initialization commits are split across `_recipe_execution`
and `_recipe_initialization`. Section pagination is split between the
page-fitting engine (`_recipe_section_pagination`, `_recipe_section_planning`)
and the `section/` sub-package's final invariant proof.

## Import boundary

Consumers import from the defining module under `autoskillit.server.recipe`,
not through a package-level re-export — this package's `__init__.py` carries
a responsibility docstring only. External production packages continue
using the `autoskillit.server` gateway.

## Section verification dependency

`_recipe_section_planning` plans page boundaries; `section/` proves the
final invariant over the resulting plan (`section/_verification.py`). The
dependency is one-way: section verification calls into
`_recipe_section_planning`, never the reverse.
