"""Backward-compat shim for ``recipe/_api_orchestration_parse.py``.

Real implementation: ``autoskillit.recipe.api_orchestration._api_orchestration_parse`` (#4951).
Preserves old import path ``autoskillit.recipe._api_orchestration_parse``.
"""

from __future__ import annotations

from autoskillit.recipe.api_orchestration._api_orchestration_parse import (  # noqa: F401
    Path,
    Recipe,
    RecipeInfo,
    _parse_and_compose,
    annotations,
    dataclasses,
    hashlib,
)

__all__ = ["_parse_and_compose"]
