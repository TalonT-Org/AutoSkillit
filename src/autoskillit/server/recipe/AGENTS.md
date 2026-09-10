# server/recipe/

Recipe artifact, delivery, execution, generation, and initialization —
relocated from `server/` (issue #4673) with no behavior change.

## Responsibilities

| Module | Owns |
|---|---|
| `_recipe_artifact.py` | Immutable recipe artifact persistence and canonical generation building |
| `_recipe_delivery.py` | Unified recipe finalization: decision, shaping, and transactional commit |
| `_recipe_delivery_helpers.py` | Delivery decision-support: attestation, margins, manifest planning |
| `_recipe_segment_delivery.py` | Canonical startup and checkpoint carriers for segmented delivery |
| `_recipe_execution.py` | Server-owned compiled recipe execution and audit admission state |
| `_recipe_generation.py` | Kitchen-scoped ownership of compiled and persisted recipe generations |
| `_recipe_initialization.py` | Server translation and post-enforcement commits for initialization |
| `_recipe_section_pagination.py` | Deterministic grammar-aware pagination for persisted recipe sections |
| `_recipe_section_planning.py` | Page boundary planning: binary search plus candidate-page fitness |

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
