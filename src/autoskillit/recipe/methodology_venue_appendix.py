"""Backward-compat shim for ``recipe/methodology_venue_appendix.py``.

Real implementation: ``autoskillit.recipe.methodology.methodology_venue_appendix`` (#4671 D).
Preserves old import path ``autoskillit.recipe.methodology_venue_appendix``.
"""

from __future__ import annotations

from autoskillit.recipe.methodology.methodology_venue_appendix import (
    _CONSTRAINT_EVALUATORS,
    _ML_SUB_AREA_CACHE,
    BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    AlternateParentDef,
    Callable,
    MLSubAreaFoldingDef,
    Path,
    VenueAppendixDef,
    VenueAppendixMatch,
    YamlFileCache,
    _has_keyword_match,
    _keyword_pattern,
    _keyword_pattern_cached,
    _load_and_parse_ml_sub_area,
    _resolve_conditional_parent,
    annotations,
    cache,
    dataclass,
    load_all_methodology_traditions,
    load_ml_sub_area_folding,
    load_yaml,
    resolve_venue_appendices,
)

__all__ = [
    "AlternateParentDef",
    "BUNDLED_METHODOLOGY_TRADITIONS_DIR",
    "Callable",
    "MLSubAreaFoldingDef",
    "Path",
    "VenueAppendixDef",
    "VenueAppendixMatch",
    "YamlFileCache",
    "_CONSTRAINT_EVALUATORS",
    "_ML_SUB_AREA_CACHE",
    "_has_keyword_match",
    "_keyword_pattern",
    "_keyword_pattern_cached",
    "_load_and_parse_ml_sub_area",
    "_resolve_conditional_parent",
    "annotations",
    "cache",
    "dataclass",
    "load_all_methodology_traditions",
    "load_ml_sub_area_folding",
    "load_yaml",
    "resolve_venue_appendices",
]
