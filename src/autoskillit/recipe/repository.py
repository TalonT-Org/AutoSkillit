"""Concrete RecipeRepository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoskillit.recipe._api import list_all, load_and_validate, validate_from_path
from autoskillit.recipe.io import find_recipe_by_name, list_recipes


class DefaultRecipeRepository:
    """Concrete RecipeRepository backed by find_recipe_by_name and list_recipes."""

    def find(self, name: str, project_dir: Path) -> Any:
        return find_recipe_by_name(name, project_dir)

    def list(self, project_dir: Path) -> Any:
        return list_recipes(project_dir)

    def load_and_validate(
        self, name: str, project_dir: Any, *, suppressed: Sequence[str] | None = None
    ) -> dict[str, Any]:
        return load_and_validate(name, project_dir=project_dir, suppressed=suppressed)

    def validate_from_path(self, script_path: Any) -> dict[str, Any]:
        return validate_from_path(script_path)

    def list_all(self, project_dir: Any | None = None) -> dict[str, Any]:
        return list_all(project_dir=project_dir)
