# server/recipe/section/

Recipe-section planning support internals — relocated from
`server/recipe_section/` (issue #4673) with no behavior change.

## Responsibilities

| Module | Owns |
|---|---|
| `_contracts.py` | Shared contracts for section planning and final verification |
| `_lifecycle.py` | One-way lifecycle notifications for section supporting state |
| `_rendering.py` | Bounded wire rendering for section tool failures |
| `_verification.py` | Final invariant proof for immutable section page plans |

## Import boundary

Consumers import from the defining module under
`autoskillit.server.recipe.section`. `_verification.py` calls into the
parent package's `_recipe_section_planning` for page-boundary planning;
that dependency is one-way and must not be reversed.
