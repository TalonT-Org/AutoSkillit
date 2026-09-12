"""Backward-compat shim for ``recipe/methodology_tradition_registry.py``.

Real implementation: ``autoskillit.recipe.methodology.methodology_tradition_registry`` (#4671 D).
Preserves old import path ``autoskillit.recipe.methodology_tradition_registry``.
"""

from __future__ import annotations

from autoskillit.recipe.methodology.methodology_tradition_registry import (
    _MISSING_MTIME,
    BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    EXPECTED_SCHEMA_VERSION,
    Any,
    MethodologyTraditionSpec,
    Path,
    VenueAppendixDef,
    _load_traditions_from_dir,
    _parse_methodology_tradition,
    _parse_venue_appendices,
    _traditions_cache,
    annotations,
    dataclass,
    dir_mtime,
    field,
    get_logger,
    get_methodology_tradition_by_name,
    is_out_of_scope_tradition,
    load_all_methodology_traditions,
    load_traditions_from_dir,
    load_yaml,
    logger,
    parse_int_field,
    parse_methodology_tradition,
    pkg_root,
)

__all__ = [
    "Any",
    "BUNDLED_METHODOLOGY_TRADITIONS_DIR",
    "EXPECTED_SCHEMA_VERSION",
    "MethodologyTraditionSpec",
    "Path",
    "VenueAppendixDef",
    "_MISSING_MTIME",
    "_load_traditions_from_dir",
    "_parse_methodology_tradition",
    "_parse_venue_appendices",
    "_traditions_cache",
    "annotations",
    "dataclass",
    "dir_mtime",
    "field",
    "get_logger",
    "get_methodology_tradition_by_name",
    "is_out_of_scope_tradition",
    "load_all_methodology_traditions",
    "load_traditions_from_dir",
    "load_yaml",
    "logger",
    "parse_int_field",
    "parse_methodology_tradition",
    "pkg_root",
]
