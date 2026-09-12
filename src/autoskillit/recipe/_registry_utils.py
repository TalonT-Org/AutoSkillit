"""Backward-compat shim for ``recipe/_registry_utils.py``.

Real implementation: ``autoskillit.recipe.helpers._registry_utils`` (#4671 D).
Preserves old import path ``autoskillit.recipe._registry_utils``.
"""

from __future__ import annotations

from autoskillit.recipe.helpers._registry_utils import (
    _MISSING_MTIME,
    EXPECTED_SCHEMA_VERSION,
    Final,
    Path,
    annotations,
    dir_mtime,
    parse_int_field,
)

__all__ = [
    "EXPECTED_SCHEMA_VERSION",
    "Final",
    "Path",
    "_MISSING_MTIME",
    "annotations",
    "dir_mtime",
    "parse_int_field",
]
