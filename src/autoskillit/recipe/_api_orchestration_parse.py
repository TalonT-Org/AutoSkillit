"""Parse the recipe YAML and compose sub-recipes into the active recipe.

Called by Phase 3 (validate) at the start of the pipeline to avoid a
duplicate parse later.

Routing rule: ``_parse_recipe``, ``pkg_root``, ``validate_recipe_structure``,
and ``load_recipe_dict_with_declarations`` are in the hub's 13-name
monkeypatch block (``tests/recipe/test_api_split.py::_ALL_MONKEYPATCH_TARGETS``),
so they route through ``_orch.{name}``. All other collaborators are imported
directly from their source modules — direct imports are function-local and
resolve at call time, so patch the source module, not ``_orch``, for those.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import autoskillit.recipe._api_orchestration as _orch
from autoskillit.recipe._recipe_composition import _build_active_recipe
from autoskillit.recipe.io import RecipeInfo
from autoskillit.recipe.schema import Recipe

__all__ = ["_parse_and_compose"]


def _parse_and_compose(
    match: RecipeInfo,
    raw_declared: str,
    temp_dir_relpath: str,
    pdir: Path,
    ingredient_overrides: dict[str, str] | None,
) -> tuple[Recipe | None, Recipe | None, Recipe | None, list[str]]:
    """YAML parse, content hashing, structural validation, sub-recipe composition.

    Returns ``(recipe, source_recipe, active_recipe, errors)``; ``recipe`` is
    ``None`` when ``data`` is not a dict. ``source_recipe`` is a frozen copy of
    ``recipe.steps`` retained for the post-prune route-consistency check.
    """
    data, _declared = _orch.load_recipe_dict_with_declarations(
        match.path, raw_text=raw_declared, temp_dir_relpath=temp_dir_relpath
    )
    if not isinstance(data, dict):
        return None, None, None, []

    recipe = _orch._parse_recipe(data, declared_data=_declared)
    from autoskillit.recipe.identity import compute_composite_hash  # noqa: PLC0415

    _recipe_bytes = match.path.read_bytes()
    recipe.content_hash = (
        match.content_hash
        if match.content_hash
        else "sha256:" + hashlib.sha256(_recipe_bytes).hexdigest()
    )
    recipe.composite_hash = compute_composite_hash(
        match.path,
        recipe,
        skills_dir=_orch.pkg_root() / "skills",
        project_dir=pdir,
        content_bytes=_recipe_bytes,
    )
    source_recipe = dataclasses.replace(
        recipe,
        steps={n: dataclasses.replace(step) for n, step in recipe.steps.items()},
    )
    errors = _orch.validate_recipe_structure(source_recipe)
    active_recipe, combined_recipe = _build_active_recipe(
        source_recipe, ingredient_overrides, pdir, temp_dir_relpath
    )
    if active_recipe is None:
        # Contract violation: _build_active_recipe returned None for a dict
        # payload. Append a structured error so the caller sees it instead of
        # an uncaught AssertionError.
        errors.append("_build_active_recipe returned None")
    if combined_recipe is not None:
        combined_errors = _orch.validate_recipe_structure(combined_recipe)
        errors.extend(f"[combined] {e}" for e in combined_errors)
    elif active_recipe is not None and any(
        step.sub_recipe is not None for step in source_recipe.steps.values()
    ):
        active_errors = _orch.validate_recipe_structure(active_recipe)
        errors.extend(f"[active] {error}" for error in active_errors if error not in errors)
    return recipe, source_recipe, active_recipe, errors
