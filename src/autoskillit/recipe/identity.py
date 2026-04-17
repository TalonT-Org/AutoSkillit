"""Recipe identity hashing — content and composite fingerprints."""

from __future__ import annotations

import hashlib
from pathlib import Path

from autoskillit.core import get_logger
from autoskillit.recipe.contracts import compute_skill_hash, resolve_skill_name
from autoskillit.recipe.io import find_sub_recipe_by_name
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.staleness_cache import compute_recipe_hash

logger = get_logger(__name__)


def compute_composite_hash(
    recipe_path: Path,
    recipe: Recipe,
    *,
    skills_dir: Path,
    project_dir: Path,
) -> str:
    """Compute a composite hash covering the recipe, referenced skills, and sub-recipes.

    The hash is deterministic: skill and sub-recipe names are sorted before
    hashing. Returns "sha256:<64-hex>".
    """
    hasher = hashlib.sha256()

    hasher.update(b"autoskillit-composite-v1\n")

    hasher.update(recipe_path.read_bytes())

    skill_names: set[str] = set()
    for step in recipe.steps.values():
        if step.tool == "run_skill" and step.message:
            name = resolve_skill_name(step.message)
            if name:
                skill_names.add(name)

    for name in sorted(skill_names):
        h = compute_skill_hash(name, skills_dir=skills_dir)
        hasher.update(f"skill:{name}:{h}\n".encode())

    sub_names: set[str] = set()
    for step in recipe.steps.values():
        if step.sub_recipe:
            sub_names.add(step.sub_recipe)

    for name in sorted(sub_names):
        sub_path = find_sub_recipe_by_name(name, project_dir)
        if sub_path and sub_path.is_file():
            sub_recipe = _load_sub_recipe_for_hash(sub_path)
            if sub_recipe is not None:
                sub_hash = compute_composite_hash(
                    sub_path,
                    sub_recipe,
                    skills_dir=skills_dir,
                    project_dir=project_dir,
                )
            else:
                sub_hash = compute_recipe_hash(sub_path)
            hasher.update(f"sub:{name}:{sub_hash}\n".encode())

    return "sha256:" + hasher.hexdigest()


def _load_sub_recipe_for_hash(path: Path) -> Recipe | None:
    """Lightweight recipe load for hash computation — no validation, no blocks."""
    from autoskillit.recipe.io import _parse_recipe  # noqa: PLC0415

    try:
        from autoskillit.core import load_yaml  # noqa: PLC0415

        data = load_yaml(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "steps" in data:
            return _parse_recipe(data)
    except Exception:
        logger.debug("sub_recipe_load_for_hash_failed", path=str(path))
    return None
