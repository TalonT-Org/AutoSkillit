"""Backward-compat shim for ``recipe/methodology_tradition_router.py``.

Real implementation: ``autoskillit.recipe.methodology.methodology_tradition_router`` (#4671 D).
Preserves old import path ``autoskillit.recipe.methodology_tradition_router``.
"""

from __future__ import annotations

from autoskillit.recipe.methodology.methodology_tradition_router import (
    MethodologyTraditionSpec,
    Path,
    TraditionRouterResult,
    UnionRuleDef,
    _count_keyword_matches,
    _keyword_pattern,
    _try_union_rules,
    annotations,
    cache,
    classify_methodology,
    dataclass,
    load_all_methodology_traditions,
)

__all__ = [
    "MethodologyTraditionSpec",
    "Path",
    "TraditionRouterResult",
    "UnionRuleDef",
    "_count_keyword_matches",
    "_keyword_pattern",
    "_try_union_rules",
    "annotations",
    "cache",
    "classify_methodology",
    "dataclass",
    "load_all_methodology_traditions",
]
