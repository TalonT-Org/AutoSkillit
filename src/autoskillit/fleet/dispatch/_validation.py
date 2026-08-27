"""Phase A — recipe validation & ingredient assembly for fleet dispatch (#4851).

Extracted from `fleet/_api.py:453-582`. Owns the pre-launch gating that turns
the caller-supplied `(recipe, task, ingredients)` into the resolved recipe,
backend, ingredient map, and identity handles the launch pipeline consumes.

On success returns a ``RecipeContext`` consumed by Phase B (``_lineage.py``).
On any rejection (recipe missing, validation failure, invalid kind) returns a
``DispatchResult`` wrapping ``DispatchRejected`` — no per-dispatch state file
exists at this stage, so ``per_dispatch_state_path=None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CodingAgentBackend,
    FleetErrorCode,
    ProcessStaleError,
    get_logger,
)
from autoskillit.fleet.state_types import (
    DispatchProvenanceTracker,
    DispatchRejected,
    DispatchResult,
)

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

_logger = get_logger(__name__)

# Recipe kinds the dispatch engine accepts. `RecipeKind` itself is a superset
# (e.g. `sub-recipe`, `dispatcher`); only `standard` and `food-truck` are
# dispatchable from the fleet entry point. Defined at module scope so the
# frozenset is constructed once at import time, not on every call.
_DISPATCHABLE_KINDS = frozenset({"standard", "food-truck"})


@dataclass
class RecipeContext:
    """Phase A output on success — passed to Phase B.

    Carries the resolved recipe, ingredient map, and backend identity that
    Phase B threads into the per-dispatch state handle, lineage preparation,
    and launch tuple. ``dispatch_name`` is preserved for logging but does not
    participate in identity resolution (``effective_name`` already folds in the
    caller override). ``provenance_snapshots`` is reserved for the orchestrator
    to record intermediate effect provenance (e.g. pre-recipe-load effects);
    Phase A does not populate it directly.
    """

    effective_name: str
    effective_ingredients: dict[str, str]
    full_recipe: Any  # Recipe
    effective_backend: Any  # CodingAgentBackend | None
    caller_backend_name: str
    recipe: str
    task: str
    recipe_obj: Any = None  # RecipeInfo — populated by Phase A, consumed by Phase B
    # Required by Phase B but not constructed in Phase A.
    dispatch_name: str | None = None
    # Provenance threading
    provenance_snapshots: dict = field(default_factory=dict)


async def run_pre_launch_gating(
    *,
    tool_ctx: ToolContext,
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None,
    dispatch_name: str | None,
    dispatch_backend: CodingAgentBackend | None = None,
    effective_backend_map: dict[str, str] | None = None,
    provenance: DispatchProvenanceTracker,
) -> RecipeContext | DispatchResult:
    """Validate the recipe, assemble ingredients, and resolve the backend.

    Mirrors `_run_dispatch` lines 453-582 in `fleet/_api.py`. Phase A does NOT
    acquire the fleet lock, mint a dispatch ID, or write any per-dispatch
    state — those concerns live in Phase B (`_lineage.py`).

    Provenance is REQUIRED: the orchestrator creates the tracker before
    invoking Phase A, so this function does not synthesize a fallback.
    """
    if tool_ctx.recipes is None:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_MANIFEST_MISSING,
                message="Recipe repository not configured.",
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    recipe_obj = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
    if recipe_obj is None:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
                message=f"Recipe '{recipe}' not found.",
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    _effective_backend = dispatch_backend if dispatch_backend is not None else tool_ctx.backend
    _caller_backend_name = tool_ctx.backend.name if tool_ctx.backend is not None else ""

    try:
        validation_result = tool_ctx.recipes.load_and_validate(
            recipe,
            tool_ctx.project_dir,
            suppressed=tool_ctx.config.migration.suppressed if tool_ctx.config else None,
            ingredient_overrides=ingredients,
            temp_dir=tool_ctx.temp_dir,
            backend_name=_effective_backend.name if _effective_backend else None,
            effective_backend_map=effective_backend_map,
        )
    except ProcessStaleError as exc:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_PROCESS_STALE,
                message=str(exc),
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )
    except Exception as exc:
        _logger.warning("load_and_validate failed for '%s'", recipe, exc_info=True)
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
                message=f"Recipe '{recipe}' could not be loaded: {exc}",
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    if not validation_result.get("valid", False):
        structural_errors = validation_result.get("errors", [])
        error_findings = [
            s for s in validation_result.get("suggestions", []) if s.get("severity") == "error"
        ]
        total_errors = len(structural_errors) + len(error_findings)
        error_parts = structural_errors[:3] + [
            f"[{f['rule']}] {f['message']}" for f in error_findings[:3]
        ]
        shown = len(error_parts)
        if total_errors > shown:
            error_parts.append(f"+{total_errors - shown} more errors")
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
                message=f"Recipe '{recipe}' has validation errors: " + "; ".join(error_parts),
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    try:
        full_recipe = tool_ctx.recipes.load(recipe_obj.path)
    except Exception as exc:
        _logger.warning("load_recipe failed for '%s'", recipe, exc_info=True)
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
                message=f"Recipe '{recipe}' could not be loaded: {exc}",
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    if full_recipe.kind not in _DISPATCHABLE_KINDS:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_INVALID_RECIPE_KIND,
                message=(
                    f"Recipe '{recipe}' has kind '{full_recipe.kind}'. "
                    "Only standard and food-truck recipes can be dispatched."
                ),
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    effective_ingredients = ingredients or {}
    if "task" in full_recipe.ingredients and "task" not in effective_ingredients:
        effective_ingredients = {"task": task, **effective_ingredients}

    from autoskillit.config import (  # noqa: PLC0415
        apply_config_authoritative_overrides,
    )

    effective_ingredients = apply_config_authoritative_overrides(
        effective_ingredients,
        full_recipe.ingredients,
        tool_ctx.project_dir,
    )

    effective_name = dispatch_name or recipe

    return RecipeContext(
        effective_name=effective_name,
        effective_ingredients=effective_ingredients,
        full_recipe=full_recipe,
        effective_backend=_effective_backend,
        caller_backend_name=_caller_backend_name,
        recipe=recipe,
        task=task,
        recipe_obj=recipe_obj,
        dispatch_name=dispatch_name,
    )
