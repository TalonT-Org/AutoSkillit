"""Phase 2 of the load pipeline: locate the recipe and read its raw YAML.

Raises ``RecipeNotFoundError`` when no recipe matches.

Routing rule: ``_t`` and ``pkg_root`` are in the hub's 13-name monkeypatch
block (``tests/recipe/test_api_split.py::_ALL_MONKEYPATCH_TARGETS``), so
they route through ``_orch.{name}``. All other collaborators are imported
directly from their source modules — direct imports are function-local and
resolve at call time, so patch the source module, not ``_orch``, for those.
"""

from __future__ import annotations

import autoskillit.recipe._api_orchestration as _orch
from autoskillit.core import RecipeNotFoundError, RecipeSource
from autoskillit.recipe._api_orchestration_types import _LoadPipelineInputs, _ValidationResult
from autoskillit.recipe.io import (
    RecipeInfo,
    find_recipe_by_name,
    substitute_scripts_placeholder,
    substitute_temp_placeholder,
)

__all__ = ["_resolve_recipe_match"]


def _resolve_recipe_match(
    name: str, pipeline_inputs: _LoadPipelineInputs, t0: float
) -> tuple[_ValidationResult, float]:
    """Find the recipe, derive ``recipes_dir``, init state. Thread ``t0`` for the chain."""
    if pipeline_inputs.normalized_recipe_info is not None:
        match: RecipeInfo | None = pipeline_inputs.normalized_recipe_info
    else:
        match = find_recipe_by_name(name, pipeline_inputs.pdir)
    t0 = _orch._t("find_recipe", t0, name)

    if match is None:
        raise RecipeNotFoundError(f"No recipe named '{name}' found")

    raw_declared = match.content if match.content is not None else match.path.read_text()
    raw = substitute_temp_placeholder(raw_declared, pipeline_inputs.temp_dir_relpath)
    raw = substitute_scripts_placeholder(raw)

    if match.source == RecipeSource.BUILTIN:
        recipes_dir = _orch.pkg_root() / "recipes"
    else:
        recipes_dir = pipeline_inputs.pdir / ".autoskillit" / "recipes"

    return (
        _ValidationResult(
            match=match,
            recipes_dir=recipes_dir,
            recipe=None,
            active_recipe=None,
            raw_declared=raw_declared,
            raw=raw,
            errors=[],
            suggestions=[],
            skip_resolutions={},
            pre_prune_steps={},
            deferred_guard_state={},
            unreachable_step_names=(),
            effective_flow_edges=(),
            finalized_projection=None,
            valid=False,
        ),
        t0,
    )
