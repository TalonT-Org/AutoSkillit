"""Backward-compat shim for ``recipe/_contracts_staleness.py``.

Real implementation: ``autoskillit.recipe.contracts._contracts_staleness`` (#4671 D).
Preserves old import path ``autoskillit.recipe._contracts_staleness``.
"""

from __future__ import annotations

from autoskillit.recipe.contracts._contracts_staleness import (
    TYPE_CHECKING,
    UTC,
    Any,
    Path,
    StaleItem,
    StalenessEntry,
    _generate_recipe_card_for_recipe,
    annotations,
    check_contract_staleness,
    compute_recipe_hash,
    compute_skill_hash,
    datetime,
    get_logger,
    load_bundled_manifest,
    logger,
    read_staleness_cache,
    stale_to_suggestions,
    write_staleness_cache,
)

__all__ = [
    "Any",
    "Path",
    "StaleItem",
    "StalenessEntry",
    "TYPE_CHECKING",
    "UTC",
    "_generate_recipe_card_for_recipe",
    "annotations",
    "check_contract_staleness",
    "compute_recipe_hash",
    "compute_skill_hash",
    "datetime",
    "get_logger",
    "load_bundled_manifest",
    "logger",
    "read_staleness_cache",
    "stale_to_suggestions",
    "write_staleness_cache",
]
