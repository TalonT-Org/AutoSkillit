"""Backward-compat shim for ``recipe/_contracts_card.py``.

Real implementation: ``autoskillit.recipe.contracts._contracts_card`` (#4671 D).
Preserves old import path ``autoskillit.recipe._contracts_card``.
"""

from __future__ import annotations

from autoskillit.recipe.contracts._contracts_card import (
    SKILL_TOOLS,
    Any,
    BlockFingerprint,
    Path,
    RecipeCard,
    Severity,
    _compute_block_fingerprint,
    _generate_recipe_card_for_recipe,
    annotations,
    atomic_write,
    bind_recipe,
    classify_step_arg_style,
    compute_skill_hash,
    count_positional_args,
    dump_yaml_str,
    extract_context_refs,
    extract_input_refs,
    generate_recipe_card,
    get_logger,
    get_skill_contract,
    load_bundled_manifest,
    load_recipe_card,
    load_yaml,
    logger,
    resolve_skill_name,
    validate_recipe_cards,
)

__all__ = [
    "Any",
    "BlockFingerprint",
    "Path",
    "RecipeCard",
    "SKILL_TOOLS",
    "Severity",
    "_compute_block_fingerprint",
    "_generate_recipe_card_for_recipe",
    "annotations",
    "atomic_write",
    "bind_recipe",
    "classify_step_arg_style",
    "compute_skill_hash",
    "count_positional_args",
    "dump_yaml_str",
    "extract_context_refs",
    "extract_input_refs",
    "generate_recipe_card",
    "get_logger",
    "get_skill_contract",
    "load_bundled_manifest",
    "load_recipe_card",
    "load_yaml",
    "logger",
    "resolve_skill_name",
    "validate_recipe_cards",
]
