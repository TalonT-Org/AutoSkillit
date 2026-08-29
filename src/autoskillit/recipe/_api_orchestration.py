"""Public driver and monkeypatch hub for recipe load-and-validate orchestration.

This module exposes :func:`load_and_validate` as the single public entry point
for the four-phase recipe load pipeline. The implementation shards live in
sibling modules:

    _api_orchestration_types        — seam-contract dataclasses
    _api_orchestration_text         — orchestration text builders
    _api_orchestration_cache        — Phase 1: cache-key derivation
    _api_orchestration_match        — Phase 2: recipe lookup
    _api_orchestration_parse        — YAML parse and sub-recipe composition
    _api_orchestration_validate     — Phase 3: validation pipeline
    _api_orchestration_assemble     — Phase 4: result assembly and cache write

The module also serves as the monkeypatch hub: it re-exports the names listed
in ``__all__`` as module-level attributes so existing tests using
``monkeypatch.setattr(orch, NAME, mock)`` and
``mock.patch("autoskillit.recipe._api_orchestration.NAME", ...)`` continue to
resolve at call time. The sibling shards access these names through
``_orch.{name}`` so the patches reach every call site.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import get_logger, pkg_root  # noqa: F401 — monkeypatch target
from autoskillit.recipe._api_orchestration_assemble import (
    _assemble_load_result,
    _finalize_recipe_steps,
)
from autoskillit.recipe._api_orchestration_cache import (
    _canonical_string_map,
    _resolve_cache_inputs,
)
from autoskillit.recipe._api_orchestration_match import _resolve_recipe_match
from autoskillit.recipe._api_orchestration_parse import _parse_and_compose
from autoskillit.recipe._api_orchestration_text import (
    _build_orchestration_rules,
    _build_stop_step_semantics,
    _infer_stop_failure,
)
from autoskillit.recipe._api_orchestration_types import _LoadPipelineInputs, _ValidationResult
from autoskillit.recipe._api_orchestration_validate import (
    _record_pipeline_error,
    _run_validation_pipeline,
)
from autoskillit.recipe._io_loading import (
    load_recipe_dict_with_declarations,  # noqa: F401 — monkeypatch
)
from autoskillit.recipe._recipe_ingredients import LoadRecipeResult
from autoskillit.recipe.contracts import (  # noqa: F401 — monkeypatch targets
    check_contract_staleness,
    load_recipe_card,
    validate_recipe_cards,
)
from autoskillit.recipe.io import (  # noqa: F401 — monkeypatch targets
    _parse_recipe,
    list_recipes,
)
from autoskillit.recipe.validator import (  # noqa: F401 — monkeypatch targets
    compute_recipe_validity,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe_structure,
)

if TYPE_CHECKING:
    from autoskillit.core import BackendCapabilities, SkillLister
    from autoskillit.recipe.io import RecipeInfo

logger = get_logger(__name__)


def _t(label: str, t0: float, name: str) -> float:
    """Log elapsed time for a pipeline stage and return current time.

    Uses structlog at DEBUG level; structlog's processor chain handles level
    filtering without requiring an explicit isEnabledFor() guard.

    Module-level so the ``monkeypatch.setattr(orch, "_t", capturing_t)``
    test at ``tests/recipe/test_api.py:1027`` reaches every call site
    through the ``_api_orchestration._t`` attribute accessed by sibling
    shards via ``_orch._t(...)``.
    """
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.debug("load_recipe_stage", recipe=name, stage=label, elapsed_ms=round(elapsed_ms, 1))
    return time.perf_counter()


def load_and_validate(
    name: str,
    project_dir: Path | None = None,
    *,
    suppressed: Sequence[str] | None = None,
    recipe_info: RecipeInfo | None = None,
    recipe_list: list[RecipeInfo] | None = None,
    resolved_defaults: dict[str, str] | None = None,
    ingredient_overrides: dict[str, str] | None = None,
    temp_dir: Path | None = None,
    temp_dir_relpath: str | None = None,
    lister: SkillLister | None = None,
    defer_unresolved: bool = False,
    backend_name: str | None = None,
    effective_backend_map: dict[str, str] | None = None,
    backend_capabilities_map: dict[str, BackendCapabilities] | None = None,
    backend_origin_map: dict[str, str] | None = None,
    include_finalized_projection: bool = False,
) -> LoadRecipeResult:
    """Load a recipe by name and run full validation.

    Raises:
        ProcessStaleError: Package directory was modified since server startup.
        RecipeNotFoundError: Named recipe could not be found.
    """
    from typing import cast

    from autoskillit.recipe import _api_cache

    t0 = time.perf_counter()

    pipeline_inputs = _resolve_cache_inputs(
        name,
        project_dir,
        suppressed=suppressed,
        recipe_info=recipe_info,
        recipe_list=recipe_list,
        resolved_defaults=resolved_defaults,
        ingredient_overrides=ingredient_overrides,
        temp_dir=temp_dir,
        temp_dir_relpath=temp_dir_relpath,
        lister=lister,
        defer_unresolved=defer_unresolved,
        backend_name=backend_name,
        effective_backend_map=effective_backend_map,
        backend_capabilities_map=backend_capabilities_map,
        backend_origin_map=backend_origin_map,
        include_finalized_projection=include_finalized_projection,
    )

    # Cache fast-path: only when cacheable AND cached entry matches.
    cached = (
        _api_cache._LOAD_CACHE.get(pipeline_inputs.cache_key)
        if pipeline_inputs.cacheable
        else None
    )
    if (
        cached is not None
        and cached.pkg_version == pipeline_inputs.pkg_version
        and cached.rule_registry_hash == pipeline_inputs.rule_registry_hash
    ):
        pm = _api_cache._path_mtime_ns(pipeline_inputs.project_recipes_dir)
        bm = _api_cache._path_mtime_ns(pipeline_inputs.builtin_dir)
        rm = _api_cache._path_mtime_ns(cached.recipe_path)
        rs = _api_cache._file_size(cached.recipe_path)
        if (
            pm == cached.project_dir_mtime
            and bm == cached.builtin_dir_mtime
            and rm == cached.recipe_mtime
            and rs == cached.recipe_size
        ):
            logger.debug("load_recipe_cache_hit", recipe=name)
            return cast(LoadRecipeResult, _api_cache._LOAD_CACHE.copy_result(cached.result))

    partial, t0 = _resolve_recipe_match(name, pipeline_inputs, t0)
    pipeline_result = _run_validation_pipeline(partial, pipeline_inputs, t0)
    result = _assemble_load_result(pipeline_result, pipeline_inputs)

    if result.get("valid", False):
        _api_cache._refresh_staleness_baseline()
    return cast(LoadRecipeResult, _api_cache._LOAD_CACHE.copy_result(result))


__all__ = [
    # Phase-symbol aliases (must remain `is`-equal to the owning shard —
    # enforced by test_phase_symbols_are_same_object_as_owning_shard).
    "_LoadPipelineInputs",  # phase-alias: _api_orchestration_types
    "_ValidationResult",  # phase-alias: _api_orchestration_types
    "_resolve_cache_inputs",  # phase-alias: _api_orchestration_cache
    "_resolve_recipe_match",  # phase-alias: _api_orchestration_match
    "_parse_and_compose",  # phase-alias: _api_orchestration_parse
    "_run_validation_pipeline",  # phase-alias: _api_orchestration_validate
    "_assemble_load_result",  # phase-alias: _api_orchestration_assemble
    "_finalize_recipe_steps",  # phase-alias: _api_orchestration_assemble
    "_record_pipeline_error",  # phase-alias: _api_orchestration_validate
    "_infer_stop_failure",  # phase-alias: _api_orchestration_text
    "_build_stop_step_semantics",  # phase-alias: _api_orchestration_text
    "_build_orchestration_rules",  # phase-alias: _api_orchestration_text
    "_canonical_string_map",  # phase-alias: _api_orchestration_cache
    # Monkeypatch-hub re-exports (must remain module attributes —
    # enforced by test_monkeypatch_targets_are_module_attributes_of_api_orchestration).
    "load_recipe_dict_with_declarations",  # monkeypatch-target: _io_loading
    "_parse_recipe",  # monkeypatch-target: recipe.io
    "load_recipe_card",  # monkeypatch-target: recipe.contracts
    "run_semantic_rules",  # monkeypatch-target: recipe.validator
    "validate_recipe_structure",  # monkeypatch-target: recipe.validator
    "list_recipes",  # monkeypatch-target: recipe.io
    "validate_recipe_cards",  # monkeypatch-target: recipe.contracts
    "check_contract_staleness",  # monkeypatch-target: recipe.contracts
    "compute_recipe_validity",  # monkeypatch-target: recipe.validator
    "findings_to_dicts",  # monkeypatch-target: recipe.validator
    "pkg_root",  # monkeypatch-target: autoskillit.core
    # Local module-level (also monkeypatch targets — same test as above).
    "_t",
    "logger",
    # Public API.
    "load_and_validate",
]
