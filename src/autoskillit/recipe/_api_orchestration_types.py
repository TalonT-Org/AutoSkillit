"""Backward-compat shim for ``recipe/_api_orchestration_types.py``.

Real implementation: ``autoskillit.recipe.api_orchestration._api_orchestration_types`` (#4951).
Preserves old import path ``autoskillit.recipe._api_orchestration_types``.
"""

from __future__ import annotations

from autoskillit.recipe.api_orchestration._api_orchestration_types import (  # noqa: F401
    Any,
    BackendCapabilities,
    FinalizedRecipeProjection,
    Path,
    Recipe,
    RecipeFlowEdge,
    RecipeInfo,
    RecipeStep,
    Sequence,
    SkillLister,
    _LoadPipelineInputs,
    _ValidationResult,
    annotations,
    dataclasses,
)

__all__ = ["_LoadPipelineInputs", "_ValidationResult"]
