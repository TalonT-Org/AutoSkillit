"""Backward-compat shim for ``recipe/_api.py``.

Real implementation: ``autoskillit.recipe.api._api`` (#4951).
Preserves old import path ``autoskillit.recipe._api``.
"""

from __future__ import annotations

from autoskillit.recipe.api._api import (  # noqa: F401
    DeferredGuard,
    ListRecipesResult,
    LoadCache,
    LoadRecipeResult,
    OpenKitchenResult,
    RecipeInfo,
    RecipeListItem,
    SkillLister,
    _build_active_recipe,
    _compute_registry_hash,
    _LoadCacheEntry,
    annotate_diagram_with_pruning,
    annotations,
    assert_no_raw_placeholders,
    bind_recipe,
    build_ingredient_rows,
    builtin_recipes_dir,
    builtin_sub_recipes_dir,
    check_contract_staleness,
    check_diagram_staleness,
    compute_recipe_validity,
    diagram_stale_to_suggestions,
    filter_pruning_false_positives,
    filter_version_rule,
    find_recipe_by_name,
    findings_to_dicts,
    format_ingredients_table,
    format_recipe_list_response,
    list_all,
    list_recipes,
    load_and_validate,
    load_recipe_card,
    load_recipe_diagram,
    load_recipe_dict_with_declarations,
    resolve_temp_dir,
    run_semantic_rules,
    stale_to_suggestions,
    substitute_scripts_placeholder,
    substitute_temp_placeholder,
    validate_from_path,
    validate_recipe_cards,
    validate_recipe_structure,
)
