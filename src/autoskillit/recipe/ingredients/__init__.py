"""IL-2 recipe ingredient types and composition (#4671 Phase D).

Sub-package of :mod:`autoskillit.recipe`. Real implementations live in this
sub-package; backward-compat shims at the old ``recipe/_recipe_*.py`` paths
preserve existing import sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._recipe_composition import (  # noqa: F401
        _MODEL_COND_RE,
        FALSY_STRINGS,
        INPUT_REF_RE,
        RECIPE_TERMINAL_TARGETS,
        SKILL_TOOLS,
        Recipe,
        RecipeFlowEdge,
        RecipeStep,
        RouteEdge,
        StepResultCondition,
        StepResultRoute,
        YAMLError,
        _analysis_edges_from_effective_routes,
        _assert_content_integrity,
        _build_active_recipe,
        _collect_all_route_targets,
        _declared_route_signatures,
        _DeferredGuardState,
        _derive_rate_limit_routes,
        _drop_sub_recipe_step,
        _effective_routing_edges,
        _effective_routing_target_errors,
        _effective_step_graph,
        _extract_routing_edges,
        _is_ingredient_truthy,
        _load_recipe_from_path,
        _merge_sub_recipe,
        _move_step_to_front,
        _parse_recipe,
        _prune_skipped_steps,
        _resolve_hidden_inputs_in_content,
        _resolve_skip_redirects,
        _rewrite_step_routes,
        _step_block_pattern,
        _strip_step_block,
        _sweep_unreachable_steps,
        _validate_effective_graph_closure,
        _validate_no_dangling_routes,
        _validate_post_sweep_effective_graph,
        _validate_route_consistency,
        bfs_reachable,
        find_sub_recipe_by_name,
        load_yaml,
    )
    from ._recipe_ingredients import (  # noqa: F401
        _GFM_DESC_MAX_WIDTH,
        _GFM_INGREDIENT_COLUMNS,
        CALLER_SOVEREIGN_INGREDIENTS,
        DeferredGuard,
        FinalizedRecipeProjection,
        ListRecipesResult,
        LoadRecipeResult,
        NotRequired,
        OpenKitchenResult,
        RecipeListItem,
        TerminalColumn,
        _ingredient_sort_key,
        _render_gfm_table,
        build_ingredient_rows,
        format_ingredients_table,
    )
    from ._recipe_raw_repair import (
        _resolve_skip_guards_in_content,
        compose_yaml,
        is_yaml_mapping_node,
    )  # noqa: F401


def __getattr__(name: str):
    """Lazily resolve symbols and submodules to avoid eager subpackage loads."""
    if name in _LAZY_MODULES:
        from importlib import import_module

        full = f"{__name__}.{name}"
        mod = import_module(full)
        globals()[name] = mod
        return mod
    mod_name = _LAZY_SYMBOL_TO_MODULE.get(name)
    if mod_name is not None:
        from importlib import import_module

        mod = import_module(f"{__name__}.{mod_name}")
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_LAZY_MODULES: frozenset[str] = frozenset(
    {
        "_recipe_composition",
        "_recipe_ingredients",
        "_recipe_raw_repair",
    }
)

_LAZY_SYMBOL_TO_MODULE: dict[str, str] = {
    "FALSY_STRINGS": "_recipe_composition",
    "INPUT_REF_RE": "_recipe_composition",
    "RECIPE_TERMINAL_TARGETS": "_recipe_composition",
    "Recipe": "_recipe_composition",
    "RecipeFlowEdge": "_recipe_composition",
    "RecipeStep": "_recipe_raw_repair",
    "RouteEdge": "_recipe_composition",
    "SKILL_TOOLS": "_recipe_composition",
    "StepResultCondition": "_recipe_composition",
    "StepResultRoute": "_recipe_composition",
    "YAMLError": "_recipe_composition",
    "_DeferredGuardState": "_recipe_composition",
    "_MODEL_COND_RE": "_recipe_composition",
    "_analysis_edges_from_effective_routes": "_recipe_composition",
    "_assert_content_integrity": "_recipe_composition",
    "_build_active_recipe": "_recipe_composition",
    "_collect_all_route_targets": "_recipe_composition",
    "_declared_route_signatures": "_recipe_composition",
    "_derive_rate_limit_routes": "_recipe_composition",
    "_drop_sub_recipe_step": "_recipe_composition",
    "_effective_routing_edges": "_recipe_composition",
    "_effective_routing_target_errors": "_recipe_composition",
    "_effective_step_graph": "_recipe_composition",
    "_extract_routing_edges": "_recipe_composition",
    "_is_ingredient_truthy": "_recipe_composition",
    "_load_recipe_from_path": "_recipe_composition",
    "_merge_sub_recipe": "_recipe_composition",
    "_move_step_to_front": "_recipe_composition",
    "_parse_recipe": "_recipe_composition",
    "_prune_skipped_steps": "_recipe_composition",
    "_resolve_hidden_inputs_in_content": "_recipe_composition",
    "_resolve_skip_redirects": "_recipe_raw_repair",
    "_rewrite_step_routes": "_recipe_composition",
    "_step_block_pattern": "_recipe_composition",
    "_strip_step_block": "_recipe_composition",
    "_sweep_unreachable_steps": "_recipe_composition",
    "_validate_effective_graph_closure": "_recipe_composition",
    "_validate_no_dangling_routes": "_recipe_composition",
    "_validate_post_sweep_effective_graph": "_recipe_composition",
    "_validate_route_consistency": "_recipe_composition",
    "bfs_reachable": "_recipe_composition",
    "find_sub_recipe_by_name": "_recipe_composition",
    "load_yaml": "_recipe_composition",
    "CALLER_SOVEREIGN_INGREDIENTS": "_recipe_ingredients",
    "DeferredGuard": "_recipe_ingredients",
    "FinalizedRecipeProjection": "_recipe_ingredients",
    "ListRecipesResult": "_recipe_ingredients",
    "LoadRecipeResult": "_recipe_ingredients",
    "NotRequired": "_recipe_ingredients",
    "OpenKitchenResult": "_recipe_ingredients",
    "RecipeListItem": "_recipe_ingredients",
    "TerminalColumn": "_recipe_ingredients",
    "_GFM_DESC_MAX_WIDTH": "_recipe_ingredients",
    "_GFM_INGREDIENT_COLUMNS": "_recipe_ingredients",
    "_ingredient_sort_key": "_recipe_ingredients",
    "_render_gfm_table": "_recipe_ingredients",
    "build_ingredient_rows": "_recipe_ingredients",
    "format_ingredients_table": "_recipe_ingredients",
    "_resolve_skip_guards_in_content": "_recipe_raw_repair",
    "compose_yaml": "_recipe_raw_repair",
    "is_yaml_mapping_node": "_recipe_raw_repair",
}

__all__ = [
    "CALLER_SOVEREIGN_INGREDIENTS",
    "DeferredGuard",
    "FALSY_STRINGS",
    "FinalizedRecipeProjection",
    "INPUT_REF_RE",
    "ListRecipesResult",
    "LoadRecipeResult",
    "NotRequired",
    "OpenKitchenResult",
    "RECIPE_TERMINAL_TARGETS",
    "Recipe",
    "RecipeFlowEdge",
    "RecipeListItem",
    "RecipeStep",
    "RouteEdge",
    "SKILL_TOOLS",
    "StepResultCondition",
    "StepResultRoute",
    "TerminalColumn",
    "YAMLError",
    "_DeferredGuardState",
    "_GFM_DESC_MAX_WIDTH",
    "_GFM_INGREDIENT_COLUMNS",
    "_MODEL_COND_RE",
    "_analysis_edges_from_effective_routes",
    "_assert_content_integrity",
    "_build_active_recipe",
    "_collect_all_route_targets",
    "_declared_route_signatures",
    "_derive_rate_limit_routes",
    "_drop_sub_recipe_step",
    "_effective_routing_edges",
    "_effective_routing_target_errors",
    "_effective_step_graph",
    "_extract_routing_edges",
    "_ingredient_sort_key",
    "_is_ingredient_truthy",
    "_load_recipe_from_path",
    "_merge_sub_recipe",
    "_move_step_to_front",
    "_parse_recipe",
    "_prune_skipped_steps",
    "_render_gfm_table",
    "_resolve_hidden_inputs_in_content",
    "_resolve_skip_guards_in_content",
    "_resolve_skip_redirects",
    "_rewrite_step_routes",
    "_step_block_pattern",
    "_strip_step_block",
    "_sweep_unreachable_steps",
    "_validate_effective_graph_closure",
    "_validate_no_dangling_routes",
    "_validate_post_sweep_effective_graph",
    "_validate_route_consistency",
    "bfs_reachable",
    "build_ingredient_rows",
    "compose_yaml",
    "find_sub_recipe_by_name",
    "format_ingredients_table",
    "is_yaml_mapping_node",
    "load_yaml",
]
