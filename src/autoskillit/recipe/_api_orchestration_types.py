"""Seam-contract dataclasses shared by every _api_orchestration shard.

Decomposed 2026-08-28 under issue #4905. The orchestrator and the seven
sibling shards all import ``_LoadPipelineInputs`` and ``_ValidationResult``
from this module so the cross-shard dataclass contract has a single source
of truth. Without this split, ``_api_orchestration`` would need to import
both ``_api_orchestration_cache`` (which needs ``_LoadPipelineInputs``) and
the types (which ``_api_orchestration_cache`` imports), producing a circular
import at module-load time.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoskillit.core import (
    BackendCapabilities,
    FinalizedRecipeProjection,
    RecipeFlowEdge,
    SkillLister,
)
from autoskillit.recipe._recipe_composition import _DeferredGuardState
from autoskillit.recipe.io import RecipeInfo
from autoskillit.recipe.schema import Recipe, RecipeStep

__all__ = ["_LoadPipelineInputs", "_ValidationResult"]


@dataclasses.dataclass(frozen=True, slots=True)
class _LoadPipelineInputs:
    name: str
    pdir: Path
    cache_key: tuple[Any, ...]
    cacheable: bool
    pkg_version: str
    rule_registry_hash: str
    project_recipes_dir: Path
    builtin_dir: Path
    effective_temp_dir: Path
    temp_dir_relpath: str
    normalized_recipe_info: RecipeInfo | None
    recipe_list: list[RecipeInfo] | None
    suppressed: Sequence[str] | None
    resolved_defaults: dict[str, str] | None
    ingredient_overrides: dict[str, str] | None
    lister: SkillLister | None
    defer_unresolved: bool
    backend_name: str | None
    effective_backend_map: dict[str, str] | None
    backend_capabilities_map: dict[str, BackendCapabilities] | None
    backend_origin_map: dict[str, str] | None
    include_finalized_projection: bool


@dataclasses.dataclass(frozen=True, slots=True)
class _ValidationResult:
    match: RecipeInfo
    recipes_dir: Path  # distinct from project_recipes_dir
    recipe: Recipe | None
    active_recipe: Recipe | None
    raw_declared: str  # pre-substitution recipe text (cached to skip re-read)
    raw: str
    errors: list[str]
    suggestions: list[dict[str, Any]]
    skip_resolutions: dict[str, bool | None]
    pre_prune_steps: dict[str, RecipeStep]
    deferred_guard_state: dict[str, _DeferredGuardState]
    unreachable_step_names: tuple[str, ...]
    effective_flow_edges: tuple[RecipeFlowEdge, ...]
    finalized_projection: FinalizedRecipeProjection | None
    valid: bool
