# server/recipe/section/

Recipe-section planning support internals — relocated from
`server/recipe_section/` (issue #4673) with no behavior change.

## Responsibilities

Shared contracts for section planning and final verification live in
`_contracts`. One-way lifecycle notifications for section supporting state
live in `_lifecycle`. Bounded wire rendering for section tool failures
lives in `_rendering`. The final invariant proof for immutable section
page plans lives in `_verification`.

## Import boundary

Consumers import from the defining module under
`autoskillit.server.recipe.section`. `_verification` calls into the
parent package's `_recipe_section_planning` for page-boundary planning;
that dependency is one-way and must not be reversed.
