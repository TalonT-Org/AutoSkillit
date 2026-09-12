"""Lazy gateway for recipe load-and-validate orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import _api_orchestration as _api_orchestration
    from . import _api_orchestration_assemble as _api_orchestration_assemble
    from . import _api_orchestration_cache as _api_orchestration_cache
    from . import _api_orchestration_match as _api_orchestration_match
    from . import _api_orchestration_parse as _api_orchestration_parse
    from . import _api_orchestration_text as _api_orchestration_text
    from . import _api_orchestration_types as _api_orchestration_types
    from . import _api_orchestration_validate as _api_orchestration_validate
    from ._api_orchestration import (
        _parse_recipe,
        _t,
        check_contract_staleness,
        compute_recipe_validity,
        findings_to_dicts,
        list_recipes,
        load_and_validate,
        load_recipe_card,
        load_recipe_dict_with_declarations,
        logger,
        pkg_root,
        run_semantic_rules,
        validate_recipe_cards,
        validate_recipe_structure,
    )
    from ._api_orchestration_assemble import (
        _assemble_load_result,
        _finalize_recipe_steps,
    )
    from ._api_orchestration_cache import _canonical_string_map, _resolve_cache_inputs
    from ._api_orchestration_match import _resolve_recipe_match
    from ._api_orchestration_parse import _parse_and_compose
    from ._api_orchestration_text import (
        _build_orchestration_rules,
        _build_stop_step_semantics,
        _infer_stop_failure,
    )
    from ._api_orchestration_types import _LoadPipelineInputs, _ValidationResult
    from ._api_orchestration_validate import _record_pipeline_error, _run_validation_pipeline


def __getattr__(name: str) -> object:
    """Lazily resolve orchestration symbols and concrete child modules."""
    if name in _LAZY_MODULES:
        from importlib import import_module

        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    module_name = _LAZY_SYMBOL_TO_MODULE.get(name)
    if module_name is not None:
        from importlib import import_module

        module = import_module(f"{__name__}.{module_name}")
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Return discoverable names without resolving lazy attributes."""
    return sorted(set(globals()) | set(__all__) | _LAZY_MODULES)


_LAZY_MODULES: frozenset[str] = frozenset(
    {
        "_api_orchestration",
        "_api_orchestration_assemble",
        "_api_orchestration_cache",
        "_api_orchestration_match",
        "_api_orchestration_parse",
        "_api_orchestration_text",
        "_api_orchestration_types",
        "_api_orchestration_validate",
    }
)

_LAZY_SYMBOL_TO_MODULE: dict[str, str] = {
    "_LoadPipelineInputs": "_api_orchestration_types",
    "_ValidationResult": "_api_orchestration_types",
    "_resolve_cache_inputs": "_api_orchestration_cache",
    "_resolve_recipe_match": "_api_orchestration_match",
    "_parse_and_compose": "_api_orchestration_parse",
    "_run_validation_pipeline": "_api_orchestration_validate",
    "_assemble_load_result": "_api_orchestration_assemble",
    "_finalize_recipe_steps": "_api_orchestration_assemble",
    "_record_pipeline_error": "_api_orchestration_validate",
    "_infer_stop_failure": "_api_orchestration_text",
    "_build_stop_step_semantics": "_api_orchestration_text",
    "_build_orchestration_rules": "_api_orchestration_text",
    "_canonical_string_map": "_api_orchestration_cache",
    "load_recipe_dict_with_declarations": "_api_orchestration",
    "_parse_recipe": "_api_orchestration",
    "load_recipe_card": "_api_orchestration",
    "run_semantic_rules": "_api_orchestration",
    "validate_recipe_structure": "_api_orchestration",
    "list_recipes": "_api_orchestration",
    "validate_recipe_cards": "_api_orchestration",
    "check_contract_staleness": "_api_orchestration",
    "compute_recipe_validity": "_api_orchestration",
    "findings_to_dicts": "_api_orchestration",
    "pkg_root": "_api_orchestration",
    "_t": "_api_orchestration",
    "logger": "_api_orchestration",
    "load_and_validate": "_api_orchestration",
}

__all__ = [
    "_LoadPipelineInputs",
    "_ValidationResult",
    "_resolve_cache_inputs",
    "_resolve_recipe_match",
    "_parse_and_compose",
    "_run_validation_pipeline",
    "_assemble_load_result",
    "_finalize_recipe_steps",
    "_record_pipeline_error",
    "_infer_stop_failure",
    "_build_stop_step_semantics",
    "_build_orchestration_rules",
    "_canonical_string_map",
    "load_recipe_dict_with_declarations",
    "_parse_recipe",
    "load_recipe_card",
    "run_semantic_rules",
    "validate_recipe_structure",
    "list_recipes",
    "validate_recipe_cards",
    "check_contract_staleness",
    "compute_recipe_validity",
    "findings_to_dicts",
    "pkg_root",
    "_t",
    "logger",
    "load_and_validate",
]
