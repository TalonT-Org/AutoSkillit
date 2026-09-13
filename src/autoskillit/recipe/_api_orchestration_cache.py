"""Backward-compat shim for ``recipe/_api_orchestration_cache.py``.

Real implementation: ``autoskillit.recipe.api_orchestration._api_orchestration_cache`` (#4951).
Preserves old import path ``autoskillit.recipe._api_orchestration_cache``.
"""

from __future__ import annotations

from autoskillit.recipe.api_orchestration._api_orchestration_cache import (  # noqa: F401
    BackendCapabilities,
    Path,
    ProcessStaleError,
    RecipeInfo,
    Sequence,
    SkillLister,
    _canonical_string_map,
    _resolve_cache_inputs,
    annotations,
    builtin_recipes_dir,
    dataclasses,
    hashlib,
    resolve_temp_dir,
)

__all__ = ["_canonical_string_map", "_resolve_cache_inputs"]
