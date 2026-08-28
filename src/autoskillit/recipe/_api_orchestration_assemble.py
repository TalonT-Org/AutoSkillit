"""Phase 4 of the load pipeline: assemble the LoadRecipeResult and write the cache.

Phase 4 owns the cache write and the orchestration text assembly
(``_stop_semantics`` and ``orchestration_rules``). No monkeypatchable
symbols are called from this phase — the cache write uses ``_api_cache``
directly and the orchestration text is built by helper functions imported
from ``_api_orchestration_text``.
"""

from __future__ import annotations

from typing import Any, cast

import autoskillit.recipe._api_cache as _api_cache
from autoskillit.core import FinalizedRecipeStep
from autoskillit.recipe._api_cache import _LoadCacheEntry
from autoskillit.recipe._api_orchestration_text import (
    _build_orchestration_rules,
    _build_stop_step_semantics,
)
from autoskillit.recipe._api_orchestration_types import _LoadPipelineInputs, _ValidationResult
from autoskillit.recipe._io_loading import assert_no_raw_placeholders
from autoskillit.recipe._recipe_composition import _DeferredGuardState
from autoskillit.recipe._recipe_ingredients import (
    DeferredGuard,
    LoadRecipeResult,
    format_ingredients_table,
)
from autoskillit.recipe.diagrams import annotate_diagram_with_pruning, load_recipe_diagram
from autoskillit.recipe.schema import Recipe

__all__ = ["_assemble_load_result", "_finalize_recipe_steps"]


def _finalize_recipe_steps(
    recipe: Recipe,
    deferred_guard_state: dict[str, _DeferredGuardState],
) -> tuple[FinalizedRecipeStep, ...]:
    """Freeze the execution-relevant fields of the active recipe steps."""
    return tuple(
        FinalizedRecipeStep(
            name=name,
            tool=step.tool,
            skill_name=step.skill_name,
            provider=step.provider,
            model=step.model,
            with_args=dict(step.with_args),
            stale_threshold=step.stale_threshold,
            idle_output_timeout=step.idle_output_timeout,
            action=step.action,
            skip_when_false=(
                deferred_guard_state[name].guard_reference
                if name in deferred_guard_state
                else step.skip_when_false
            ),
        )
        for name, step in recipe.steps.items()
    )


def _assemble_load_result(
    pipeline_result: _ValidationResult, pipeline_inputs: _LoadPipelineInputs
) -> LoadRecipeResult:
    """Build the user-visible ``LoadRecipeResult`` and write the cache entry.

    Cache write is guarded by ``cacheable`` (caller-supplied non-None lister
    disables caching).
    """
    match = pipeline_result.match
    recipes_dir = pipeline_result.recipes_dir
    raw = pipeline_result.raw
    errors = pipeline_result.errors
    suggestions = pipeline_result.suggestions
    valid = pipeline_result.valid
    recipe = pipeline_result.recipe
    active_recipe = pipeline_result.active_recipe
    _skip_resolutions = pipeline_result.skip_resolutions
    _pre_prune_steps = pipeline_result.pre_prune_steps
    _deferred_guard_state = pipeline_result.deferred_guard_state
    _unreachable_step_names = pipeline_result.unreachable_step_names
    _effective_flow_edges = pipeline_result.effective_flow_edges
    _finalized_projection = pipeline_result.finalized_projection

    name = pipeline_inputs.name
    project_recipes_dir = pipeline_inputs.project_recipes_dir
    builtin_dir = pipeline_inputs.builtin_dir
    pkg_version = pipeline_inputs.pkg_version
    _rule_hash = pipeline_inputs.rule_registry_hash
    cache_key = pipeline_inputs.cache_key
    cacheable = pipeline_inputs.cacheable
    resolved_defaults = pipeline_inputs.resolved_defaults
    include_finalized_projection = pipeline_inputs.include_finalized_projection

    diagram: str | None = load_recipe_diagram(name, recipes_dir)
    if diagram is not None and _skip_resolutions:
        diagram = annotate_diagram_with_pruning(diagram, _skip_resolutions)

    _serving_recipe = active_recipe if active_recipe is not None else recipe
    ing_table = (
        format_ingredients_table(_serving_recipe, resolved_defaults=resolved_defaults)
        if _serving_recipe is not None
        else None
    )

    _hidden_names = (
        frozenset(
            n
            for n, ing in (active_recipe.ingredients or {}).items()
            if getattr(ing, "hidden", False)
        )
        if active_recipe is not None
        else None
    )
    assert_no_raw_placeholders(raw, context=name, hidden_ingredient_names=_hidden_names)
    result: dict[str, Any] = {
        "content": raw,
        "errors": errors,
        "diagram": diagram,
        "suggestions": suggestions,
        "valid": valid,
    }
    if _serving_recipe is not None and _serving_recipe.summary:
        result["summary"] = _serving_recipe.summary
    if _serving_recipe is not None and _serving_recipe.kitchen_rules:
        result["kitchen_rules"] = _serving_recipe.kitchen_rules
    if _serving_recipe is not None and _serving_recipe.requires_packs:
        result["requires_packs"] = _serving_recipe.requires_packs
    if _serving_recipe is not None and _serving_recipe.requires_features:
        result["requires_features"] = _serving_recipe.requires_features
    if ing_table:
        result["ingredients_table"] = ing_table
    # Two delivery paths: orchestration_rules embeds text for Channel A;
    # stop_step_semantics is a dedicated field for Channel B.
    _stop_semantics = _build_stop_step_semantics(active_recipe) if active_recipe else ""
    result["orchestration_rules"] = _build_orchestration_rules(
        active_recipe, stop_semantics=_stop_semantics
    )
    result["stop_step_semantics"] = _stop_semantics
    result["content_hash"] = recipe.content_hash if recipe else ""
    result["composite_hash"] = recipe.composite_hash if recipe else ""
    result["recipe_version"] = recipe.recipe_version if recipe else None

    _deferred_guard_list: list[DeferredGuard] = []
    for _dg_step, (_dg_ref, _dg_target) in _deferred_guard_state.items():
        if _skip_resolutions.get(_dg_step) is None:
            _dg_ingredient = (
                _dg_ref[len("inputs.") :] if _dg_ref and _dg_ref.startswith("inputs.") else _dg_ref
            )
            _dg_ing_obj = (
                (active_recipe.ingredients or {}).get(_dg_ingredient)
                if active_recipe and _dg_ingredient
                else None
            )
            _dg_default = (
                str(_dg_ing_obj.default)
                if _dg_ing_obj is not None and getattr(_dg_ing_obj, "default", None) is not None
                else None
            )
            if _dg_ingredient is not None:
                _deferred_guard_list.append(
                    {"step": _dg_step, "ingredient": _dg_ingredient, "default": _dg_default}
                )
    if _deferred_guard_list:
        result["deferred_guards"] = _deferred_guard_list
    if active_recipe is not None:
        if include_finalized_projection and _finalized_projection is not None:
            result["_finalized_projection"] = _finalized_projection
        result["post_prune_step_names"] = list(active_recipe.steps.keys())
        result["unreachable_step_names"] = list(_unreachable_step_names)
        _step_names_set = set(active_recipe.steps)
        result["post_prune_routing_edges"] = sorted(
            {edge.target for edge in _effective_flow_edges if edge.target in _step_names_set}
        )
    if cacheable:
        entry = _LoadCacheEntry(
            recipe_path=match.path,
            recipe_mtime=_api_cache._path_mtime_ns(match.path),
            recipe_size=_api_cache._file_size(match.path),
            project_dir_mtime=_api_cache._path_mtime_ns(project_recipes_dir),
            builtin_dir_mtime=_api_cache._path_mtime_ns(builtin_dir),
            pkg_version=pkg_version,
            rule_registry_hash=_rule_hash,
            result=result,
        )
        _api_cache._LOAD_CACHE.put(cache_key, entry)

    return cast(LoadRecipeResult, result)
