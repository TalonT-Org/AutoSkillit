"""Phase 1 of the load pipeline: derive the cache key for the current request.

Pure given inputs; reads staleness probes and registry hashes from ``_api_cache``.

Moved 2026-08-28 from ``_api_orchestration.py`` under issue #4905.

Monkeypatch contract: every call site that originally read ``pkg_root()``
directly now goes through ``_orch.pkg_root()`` so the existing
``monkeypatch.setattr(orch, "pkg_root", ...)`` test at
``tests/recipe/test_api.py:1915`` reaches the cache shard.
"""
from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Sequence
from pathlib import Path

import autoskillit.recipe._api_cache as _api_cache
import autoskillit.recipe._api_orchestration as _orch
from autoskillit.core import BackendCapabilities, ProcessStaleError, SkillLister, resolve_temp_dir
from autoskillit.recipe._api_orchestration_types import _LoadPipelineInputs
from autoskillit.recipe.io import RecipeInfo, builtin_recipes_dir

__all__ = ["_canonical_string_map", "_resolve_cache_inputs"]


def _canonical_string_map(mapping: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(mapping.items())) if mapping else ()


def _resolve_cache_inputs(
    name: str,
    project_dir: Path | None,
    *,
    suppressed: Sequence[str] | None,
    recipe_info: RecipeInfo | None,
    recipe_list: list[RecipeInfo] | None,
    resolved_defaults: dict[str, str] | None,
    ingredient_overrides: dict[str, str] | None,
    temp_dir: Path | None,
    temp_dir_relpath: str | None,
    lister: SkillLister | None,
    defer_unresolved: bool,
    backend_name: str | None,
    effective_backend_map: dict[str, str] | None,
    backend_capabilities_map: dict[str, BackendCapabilities] | None,
    backend_origin_map: dict[str, str] | None,
    include_finalized_projection: bool,
) -> _LoadPipelineInputs:
    """Process staleness check + cache-key construction + rule_hash bundling."""
    if _api_cache._check_process_staleness():
        if not _api_cache._STALENESS_CACHES_CLEARED:
            _api_cache._clear_stale_caches()
        raise ProcessStaleError(
            "Process is running stale code — package directory was modified on disk "
            "since server startup."
        )

    _pdir = (project_dir if project_dir is not None else Path.cwd()).absolute()
    pkg_version = _api_cache._get_pkg_version()
    project_recipes_dir = _pdir / ".autoskillit" / "recipes"
    builtin_dir = builtin_recipes_dir()
    from autoskillit.recipe.experiment_type_registry import (  # noqa: PLC0415
        BUNDLED_EXPERIMENT_TYPES_DIR,
    )
    from autoskillit.recipe.methodology_tradition_registry import (  # noqa: PLC0415
        BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    )

    _exp_types_hash = _api_cache._compute_registry_hash(BUNDLED_EXPERIMENT_TYPES_DIR)
    _user_exp_hash = _api_cache._compute_registry_hash(_pdir / ".autoskillit" / "experiment-types")
    _method_traditions_hash = _api_cache._compute_registry_hash(BUNDLED_METHODOLOGY_TRADITIONS_DIR)
    _user_method_traditions_hash = _api_cache._compute_registry_hash(
        _pdir / ".autoskillit" / "methodology-traditions"
    )
    _temp_relpath = temp_dir_relpath or ".autoskillit/temp"
    _default_temp_dir = resolve_temp_dir(_pdir, None).absolute()
    _effective_temp_dir = temp_dir.absolute() if temp_dir is not None else _default_temp_dir
    _temp_dir_key = None if _effective_temp_dir == _default_temp_dir else str(_effective_temp_dir)
    _normalized_recipe_info = (
        dataclasses.replace(recipe_info, path=recipe_info.path.absolute())
        if recipe_info is not None
        else None
    )
    _recipe_info_key = (
        (
            str(_normalized_recipe_info.path),
            _normalized_recipe_info.source.value,
            _normalized_recipe_info.content_hash,
            (
                hashlib.sha256(_normalized_recipe_info.content.encode()).hexdigest()
                if _normalized_recipe_info.content is not None
                else None
            ),
        )
        if _normalized_recipe_info is not None
        else None
    )
    _recipe_list_key = (
        tuple(sorted({info.name for info in recipe_list})) if recipe_list is not None else None
    )
    cacheable = lister is None
    _ml_sub_area_path = BUNDLED_METHODOLOGY_TRADITIONS_DIR / "_ml_sub_area_folding.yaml"
    _manifest_mtime = _api_cache._path_mtime_ns(
        _orch.pkg_root() / "recipe" / "skill_contracts.yaml"
    )
    _manifest_size = _api_cache._file_size(
        _orch.pkg_root() / "recipe" / "skill_contracts.yaml"
    )
    _budgets_mtime = _api_cache._path_mtime_ns(
        _orch.pkg_root() / "recipe" / "block_budgets.yaml"
    )
    _budgets_size = _api_cache._file_size(
        _orch.pkg_root() / "recipe" / "block_budgets.yaml"
    )
    _ml_sub_area_mtime = _api_cache._path_mtime_ns(_ml_sub_area_path)
    _ml_sub_area_size = _api_cache._file_size(_ml_sub_area_path)
    cache_key = (
        name,
        _temp_relpath,
        _temp_dir_key,
        str(_pdir),
        tuple(sorted(suppressed)) if suppressed else (),
        _recipe_info_key,
        _recipe_list_key,
        _canonical_string_map(resolved_defaults),
        _canonical_string_map(ingredient_overrides),
        defer_unresolved,
        _exp_types_hash,
        _user_exp_hash,
        _method_traditions_hash,
        _user_method_traditions_hash,
        backend_name,
        _canonical_string_map(effective_backend_map),
        tuple(sorted(backend_capabilities_map.items())) if backend_capabilities_map else (),
        _canonical_string_map(backend_origin_map),
        include_finalized_projection,
        _manifest_mtime,
        _manifest_size,
        _budgets_mtime,
        _budgets_size,
        _ml_sub_area_mtime,
        _ml_sub_area_size,
    )

    from autoskillit.recipe import registry as _registry  # noqa: PLC0415

    # lazy-registry: global set by _finalize_registry()
    _rule_hash: str = _registry.RULE_REGISTRY_HASH  # pyright: ignore[reportAttributeAccessIssue]
    if not _rule_hash:
        _orch.logger.warning("RULE_REGISTRY_HASH is empty — _finalize_registry() was never called")

    return _LoadPipelineInputs(
        name=name,
        pdir=_pdir,
        cache_key=cache_key,
        cacheable=cacheable,
        pkg_version=pkg_version,
        rule_registry_hash=_rule_hash,
        project_recipes_dir=project_recipes_dir,
        builtin_dir=builtin_dir,
        effective_temp_dir=_effective_temp_dir,
        temp_dir_relpath=_temp_relpath,
        normalized_recipe_info=_normalized_recipe_info,
        recipe_list=recipe_list,
        suppressed=suppressed,
        resolved_defaults=resolved_defaults,
        ingredient_overrides=ingredient_overrides,
        lister=lister,
        defer_unresolved=defer_unresolved,
        backend_name=backend_name,
        effective_backend_map=effective_backend_map,
        backend_capabilities_map=backend_capabilities_map,
        backend_origin_map=backend_origin_map,
        include_finalized_projection=include_finalized_projection,
    )
