"""Backward-compat shim for ``recipe/_skill_helpers.py``.

Real implementation: ``autoskillit.recipe.helpers._skill_helpers`` (#4671 D).
Preserves old import path ``autoskillit.recipe._skill_helpers``.
"""

from __future__ import annotations

from autoskillit.recipe.helpers._skill_helpers import (
    _SKILL_CATEGORY_CACHE,
    _SKILL_NAMES_CACHE,
    _SKILL_TOKEN_RE,
    MULTIPART_SKILL_NAMES,
    SKILL_SEARCH_DIRS,
    TYPE_CHECKING,
    Path,
    SkillLister,
    _get_bundled_skill_names,
    _get_skill_category_map,
    _has_dynamic_skill_name,
    _resolve_skill_md,
    annotations,
    bound_skill_name,
    get_allowed_values_for_skill,
    get_logger,
    logger,
    pkg_root,
)

__all__ = [
    "MULTIPART_SKILL_NAMES",
    "Path",
    "SKILL_SEARCH_DIRS",
    "SkillLister",
    "TYPE_CHECKING",
    "_SKILL_CATEGORY_CACHE",
    "_SKILL_NAMES_CACHE",
    "_SKILL_TOKEN_RE",
    "_get_bundled_skill_names",
    "_get_skill_category_map",
    "_has_dynamic_skill_name",
    "_resolve_skill_md",
    "annotations",
    "bound_skill_name",
    "get_allowed_values_for_skill",
    "get_logger",
    "logger",
    "pkg_root",
]
