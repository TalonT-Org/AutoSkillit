"""IL-2 recipe methodology tradition registry and disambiguation (#4671 Phase D).

Sub-package of :mod:`autoskillit.recipe`. Real implementations live in this
sub-package; backward-compat shims at the old ``recipe/methodology_*.py`` and
``recipe/experiment_type_registry.py`` paths preserve existing import sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .experiment_type_registry import (
        _SILENT_THRESHOLD,
        BUNDLED_EXPERIMENT_TYPES_DIR,
        ExperimentTypeSpec,
        _exp_types_cache,
        _load_types_from_dir,
        _parse_bool_field,
        _parse_experiment_type,
        get_experiment_type_by_name,
        is_silent_type,
        load_all_experiment_types,
        load_types_from_dir,
        parse_experiment_type,
    )  # noqa: F401
    from .methodology_disambiguation import (  # noqa: F401
        BUNDLED_METHODOLOGY_TRADITIONS_DIR,
        CrossTraditionOverlapDef,
        DisambiguationExceptionDef,
        DisambiguationResult,
        DisambiguationRuleDef,
        _rule_matches,
        disambiguate,
        load_disambiguation_rules,
        load_yaml,
        pkg_root,
    )
    from .methodology_tradition_registry import (  # noqa: F401
        _MISSING_MTIME,
        EXPECTED_SCHEMA_VERSION,
        MethodologyTraditionSpec,
        VenueAppendixDef,
        _load_traditions_from_dir,
        _parse_methodology_tradition,
        _parse_venue_appendices,
        _traditions_cache,
        dir_mtime,
        get_logger,
        get_methodology_tradition_by_name,
        is_out_of_scope_tradition,
        load_all_methodology_traditions,
        load_traditions_from_dir,
        logger,
        parse_int_field,
        parse_methodology_tradition,
    )
    from .methodology_tradition_router import (  # noqa: F401
        TraditionRouterResult,
        UnionRuleDef,
        _count_keyword_matches,
        _keyword_pattern,
        _try_union_rules,
        classify_methodology,
    )
    from .methodology_venue_appendix import (  # noqa: F401
        _CONSTRAINT_EVALUATORS,
        _ML_SUB_AREA_CACHE,
        AlternateParentDef,
        MLSubAreaFoldingDef,
        VenueAppendixMatch,
        YamlFileCache,
        _has_keyword_match,
        _keyword_pattern_cached,
        _load_and_parse_ml_sub_area,
        _resolve_conditional_parent,
        load_ml_sub_area_folding,
        resolve_venue_appendices,
    )


def __getattr__(name: str):
    """Lazily resolve symbols and submodules to avoid eager subpackage loads."""
    if name in _LAZY_MODULES:
        from importlib import import_module

        full = f"{__name__}.{name}"
        mod = import_module(full)
        globals()[name] = mod
        return mod
    mod_name = _LAZY_SYMBOL_TO_MODULE.get(name)
    if mod_name is not None:
        from importlib import import_module

        mod = import_module(f"{__name__}.{mod_name}")
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_LAZY_MODULES: frozenset[str] = frozenset(
    {
        "methodology_disambiguation",
        "methodology_tradition_registry",
        "methodology_tradition_router",
        "methodology_venue_appendix",
        "experiment_type_registry",
    }
)

_LAZY_SYMBOL_TO_MODULE: dict[str, str] = {
    "BUNDLED_METHODOLOGY_TRADITIONS_DIR": "methodology_venue_appendix",
    "CrossTraditionOverlapDef": "methodology_disambiguation",
    "DisambiguationExceptionDef": "methodology_disambiguation",
    "DisambiguationResult": "methodology_disambiguation",
    "DisambiguationRuleDef": "methodology_disambiguation",
    "_rule_matches": "methodology_disambiguation",
    "disambiguate": "methodology_disambiguation",
    "load_disambiguation_rules": "methodology_disambiguation",
    "load_yaml": "experiment_type_registry",
    "pkg_root": "experiment_type_registry",
    "EXPECTED_SCHEMA_VERSION": "experiment_type_registry",
    "MethodologyTraditionSpec": "methodology_tradition_registry",
    "VenueAppendixDef": "methodology_venue_appendix",
    "_MISSING_MTIME": "experiment_type_registry",
    "_load_traditions_from_dir": "methodology_tradition_registry",
    "_parse_methodology_tradition": "methodology_tradition_registry",
    "_parse_venue_appendices": "methodology_tradition_registry",
    "_traditions_cache": "methodology_tradition_registry",
    "dir_mtime": "experiment_type_registry",
    "get_logger": "experiment_type_registry",
    "get_methodology_tradition_by_name": "methodology_tradition_registry",
    "is_out_of_scope_tradition": "methodology_tradition_registry",
    "load_all_methodology_traditions": "methodology_tradition_registry",
    "load_traditions_from_dir": "methodology_tradition_registry",
    "logger": "experiment_type_registry",
    "parse_int_field": "experiment_type_registry",
    "parse_methodology_tradition": "methodology_tradition_registry",
    "TraditionRouterResult": "methodology_tradition_router",
    "UnionRuleDef": "methodology_tradition_router",
    "_count_keyword_matches": "methodology_tradition_router",
    "_keyword_pattern": "methodology_venue_appendix",
    "_try_union_rules": "methodology_tradition_router",
    "classify_methodology": "methodology_tradition_router",
    "AlternateParentDef": "methodology_venue_appendix",
    "MLSubAreaFoldingDef": "methodology_venue_appendix",
    "VenueAppendixMatch": "methodology_venue_appendix",
    "YamlFileCache": "methodology_venue_appendix",
    "_CONSTRAINT_EVALUATORS": "methodology_venue_appendix",
    "_ML_SUB_AREA_CACHE": "methodology_venue_appendix",
    "_has_keyword_match": "methodology_venue_appendix",
    "_keyword_pattern_cached": "methodology_venue_appendix",
    "_load_and_parse_ml_sub_area": "methodology_venue_appendix",
    "_resolve_conditional_parent": "methodology_venue_appendix",
    "load_ml_sub_area_folding": "methodology_venue_appendix",
    "resolve_venue_appendices": "methodology_venue_appendix",
    "BUNDLED_EXPERIMENT_TYPES_DIR": "experiment_type_registry",
    "ExperimentTypeSpec": "experiment_type_registry",
    "_SILENT_THRESHOLD": "experiment_type_registry",
    "_exp_types_cache": "experiment_type_registry",
    "_load_types_from_dir": "experiment_type_registry",
    "_parse_bool_field": "experiment_type_registry",
    "_parse_experiment_type": "experiment_type_registry",
    "get_experiment_type_by_name": "experiment_type_registry",
    "is_silent_type": "experiment_type_registry",
    "load_all_experiment_types": "experiment_type_registry",
    "load_types_from_dir": "experiment_type_registry",
    "parse_experiment_type": "experiment_type_registry",
}

__all__ = [
    "AlternateParentDef",
    "BUNDLED_EXPERIMENT_TYPES_DIR",
    "BUNDLED_METHODOLOGY_TRADITIONS_DIR",
    "CrossTraditionOverlapDef",
    "DisambiguationExceptionDef",
    "DisambiguationResult",
    "DisambiguationRuleDef",
    "EXPECTED_SCHEMA_VERSION",
    "ExperimentTypeSpec",
    "MLSubAreaFoldingDef",
    "MethodologyTraditionSpec",
    "TraditionRouterResult",
    "UnionRuleDef",
    "VenueAppendixDef",
    "VenueAppendixMatch",
    "YamlFileCache",
    "_CONSTRAINT_EVALUATORS",
    "_MISSING_MTIME",
    "_ML_SUB_AREA_CACHE",
    "_SILENT_THRESHOLD",
    "_count_keyword_matches",
    "_exp_types_cache",
    "_has_keyword_match",
    "_keyword_pattern",
    "_keyword_pattern_cached",
    "_load_and_parse_ml_sub_area",
    "_load_traditions_from_dir",
    "_load_types_from_dir",
    "_parse_bool_field",
    "_parse_experiment_type",
    "_parse_methodology_tradition",
    "_parse_venue_appendices",
    "_resolve_conditional_parent",
    "_rule_matches",
    "_traditions_cache",
    "_try_union_rules",
    "classify_methodology",
    "dir_mtime",
    "disambiguate",
    "get_experiment_type_by_name",
    "get_logger",
    "get_methodology_tradition_by_name",
    "is_out_of_scope_tradition",
    "is_silent_type",
    "load_all_experiment_types",
    "load_all_methodology_traditions",
    "load_disambiguation_rules",
    "load_ml_sub_area_folding",
    "load_traditions_from_dir",
    "load_types_from_dir",
    "load_yaml",
    "logger",
    "parse_experiment_type",
    "parse_int_field",
    "parse_methodology_tradition",
    "pkg_root",
    "resolve_venue_appendices",
]
