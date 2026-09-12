"""Backward-compat shim for ``recipe/methodology_disambiguation.py``.

Real implementation: ``autoskillit.recipe.methodology.methodology_disambiguation`` (#4671 D).
Preserves old import path ``autoskillit.recipe.methodology_disambiguation``.
"""

from __future__ import annotations

from autoskillit.recipe.methodology.methodology_disambiguation import (
    BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    CrossTraditionOverlapDef,
    DisambiguationExceptionDef,
    DisambiguationResult,
    DisambiguationRuleDef,
    Literal,
    Path,
    _rule_matches,
    annotations,
    dataclass,
    disambiguate,
    load_disambiguation_rules,
    load_yaml,
    pkg_root,
)

__all__ = [
    "BUNDLED_METHODOLOGY_TRADITIONS_DIR",
    "CrossTraditionOverlapDef",
    "DisambiguationExceptionDef",
    "DisambiguationResult",
    "DisambiguationRuleDef",
    "Literal",
    "Path",
    "_rule_matches",
    "annotations",
    "dataclass",
    "disambiguate",
    "load_disambiguation_rules",
    "load_yaml",
    "pkg_root",
]
