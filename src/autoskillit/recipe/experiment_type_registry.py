"""Backward-compat shim for ``recipe/experiment_type_registry.py``.

Real implementation: ``autoskillit.recipe.methodology.experiment_type_registry`` (#4671 D).
Preserves old import path ``autoskillit.recipe.experiment_type_registry``.
"""

from __future__ import annotations

from autoskillit.recipe.methodology.experiment_type_registry import (
    _MISSING_MTIME,
    _SILENT_THRESHOLD,
    BUNDLED_EXPERIMENT_TYPES_DIR,
    EXPECTED_SCHEMA_VERSION,
    Any,
    ExperimentTypeSpec,
    Path,
    _exp_types_cache,
    _load_types_from_dir,
    _parse_bool_field,
    _parse_experiment_type,
    annotations,
    dataclass,
    dir_mtime,
    field,
    get_experiment_type_by_name,
    get_logger,
    is_silent_type,
    load_all_experiment_types,
    load_types_from_dir,
    load_yaml,
    logger,
    parse_experiment_type,
    parse_int_field,
    pkg_root,
)

__all__ = [
    "Any",
    "BUNDLED_EXPERIMENT_TYPES_DIR",
    "EXPECTED_SCHEMA_VERSION",
    "ExperimentTypeSpec",
    "Path",
    "_MISSING_MTIME",
    "_SILENT_THRESHOLD",
    "_exp_types_cache",
    "_load_types_from_dir",
    "_parse_bool_field",
    "_parse_experiment_type",
    "annotations",
    "dataclass",
    "dir_mtime",
    "field",
    "get_experiment_type_by_name",
    "get_logger",
    "is_silent_type",
    "load_all_experiment_types",
    "load_types_from_dir",
    "load_yaml",
    "logger",
    "parse_experiment_type",
    "parse_int_field",
    "pkg_root",
]
