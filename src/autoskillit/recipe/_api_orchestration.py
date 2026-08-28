"""Recipe load-and-validate orchestration. See issue #4860.

Decomposed 2026-08-28 under issue #4905 along the four-phase pipeline. This
module remains the public driver and the monkeypatch hub for the validation
pipeline. Implementation shards live in sibling modules:

    _api_orchestration_types        — seam-contract dataclasses
    _api_orchestration_text         — orchestration text builders
    _api_orchestration_cache        — Phase 1: cache-key derivation
    _api_orchestration_match        — Phase 2: recipe lookup
    _api_orchestration_parse        — YAML parse and sub-recipe composition
    _api_orchestration_validate     — Phase 3: validation pipeline
    _api_orchestration_assemble     — Phase 4: result assembly and cache write

Monkeypatch hub contract: the 13 names listed below are module-level
attributes of this module so ``monkeypatch.setattr(orch, NAME, mock)``
(used in ``tests/recipe/test_api.py``,
``tests/recipe/test_api_orchestration.py``,
``tests/recipe/test_recipe_composition_vacuous_gate.py``,
``tests/server/test_load_recipe_exception_handling.py``, and
``tests/server/test_tools_load_recipe.py``) and
``mock.patch("autoskillit.recipe._api_orchestration.NAME", ...)`` continue to
resolve at call time. The sibling shards access these names through
``_orch.{name}`` so the patches reach every call site:

    load_recipe_dict_with_declarations
    _parse_recipe
    load_recipe_card
    run_semantic_rules
    validate_recipe_structure
    list_recipes
    validate_recipe_cards
    check_contract_staleness
    compute_recipe_validity
    findings_to_dicts
    pkg_root
    _t (defined in this module)
    logger (defined in this module)

AST-guard invariants preserved:

* The literal token ``SkillLister`` (re-imported with ``# noqa: F401``)
  satisfies ``tests/arch/test_subpackage_isolation_module_boundaries.py``.
* The literal string ``rule_registry_hash`` appears inside
  ``load_and_validate``'s body (the cache-hit comparison) to satisfy
  ``tests/arch/test_recipe_rule_registration.py``.
* ``load_and_validate``'s body contains no ``return {..."error": ...}``
  statement to satisfy ``tests/arch/test_no_error_dict_return.py``.
* ``_prune_skipped_steps`` precedes ``run_semantic_rules`` in
  ``_run_validation_pipeline`` (now in ``_api_orchestration_validate.py``)
  to satisfy ``tests/arch/test_pipeline_ordering.py`` (retargeted in Step 9b).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path

from autoskillit.core import (
    BackendCapabilities,  # noqa: F401 — re-exported for type visibility
    SkillLister,  # noqa: F401 — preserved for module-boundary literal-string check
    get_logger,
    pkg_root,  # noqa: F401 — monkeypatch target (tests/recipe/test_api.py:1915)
)
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
from autoskillit.recipe.contracts import (  # noqa: F401 — monkeypatch targets
    check_contract_staleness,
    load_recipe_card,
    validate_recipe_cards,
)
from autoskillit.recipe.io import (  # noqa: F401 — monkeypatch targets
    RecipeInfo,
    _parse_recipe,
    list_recipes,
)
from autoskillit.recipe.validator import (  # noqa: F401 — monkeypatch targets
    compute_recipe_validity,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe_structure,
)

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
):
    """Load a recipe by name and run full validation.

    Raises:
        ProcessStaleError: Package directory was modified since server startup.
        RecipeNotFoundError: Named recipe could not be found.

    Body preserved verbatim from the original ``_api_orchestration.py`` —
    the AST guards require the literal ``rule_registry_hash`` and forbid
    ``return {..."error": ...}`` patterns inside this function's body.
    """
    from typing import cast

    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._recipe_ingredients import LoadRecipeResult

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
    # Phase symbols (identity aliases to sibling shard definitions).
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
    # Monkeypatch hub re-exports (13 names).
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
    # Local module-level.
    "_t",
    "logger",
    # Public API.
    "load_and_validate",
]
