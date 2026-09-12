"""Backward-compat shim for ``recipe/_api_listing.py``.

Real implementation: ``autoskillit.recipe.api._api_listing`` (#4951).
Preserves old import path ``autoskillit.recipe._api_listing``.
"""

from __future__ import annotations

from autoskillit.recipe.api._api_listing import (  # noqa: F401
    Any,
    BackendCapabilities,
    LoadResult,
    Path,
    RecipeInfo,
    RecipeListItem,
    RuleFinding,
    SkillLister,
    YAMLError,
    annotations,
    build_quality_dict,
    compute_recipe_validity,
    filter_pruning_false_positives,
    findings_to_dicts,
    format_recipe_list_response,
    get_logger,
    list_all,
    list_recipes,
    load_recipe_card,
    load_yaml,
    logger,
    make_validation_context,
    run_semantic_rules,
    substitute_temp_placeholder,
    validate_from_path,
    validate_recipe_cards,
    validate_recipe_structure,
)
