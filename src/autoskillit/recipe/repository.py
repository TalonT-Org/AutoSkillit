"""Concrete RecipeRepository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoskillit.recipe._api import list_all, load_and_validate, validate_from_path
from autoskillit.recipe.io import builtin_recipes_dir, list_recipes


def _dir_mtime(path: Path) -> float:
    """Return directory mtime as float, or 0.0 if the path does not exist."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class DefaultRecipeRepository:
    """Concrete RecipeRepository backed by list_recipes with in-memory mtime cache."""

    def __init__(self) -> None:
        self._cached_list: Any | None = None
        self._cached_project_dir: Path | None = None
        self._cached_project_mtime: float = 0.0
        self._cached_builtin_mtime: float = 0.0

    def _get_list(self, project_dir: Path) -> Any:
        pm = _dir_mtime(project_dir / ".autoskillit" / "recipes")
        bm = _dir_mtime(builtin_recipes_dir())
        if (
            self._cached_list is not None
            and self._cached_project_dir == project_dir
            and self._cached_project_mtime == pm
            and self._cached_builtin_mtime == bm
        ):
            return self._cached_list
        result = list_recipes(project_dir)
        self._cached_list = result
        self._cached_project_dir = project_dir
        self._cached_project_mtime = pm
        self._cached_builtin_mtime = bm
        return result

    def find(self, name: str, project_dir: Path) -> Any:
        result = self._get_list(project_dir)
        return next((r for r in result.items if r.name == name), None)

    def list(self, project_dir: Path) -> Any:
        return self._get_list(project_dir)

    def load_and_validate(
        self, name: str, project_dir: Any, *, suppressed: Sequence[str] | None = None
    ) -> dict[str, Any]:
        return load_and_validate(name, project_dir=project_dir, suppressed=suppressed)

    def validate_from_path(self, script_path: Any) -> dict[str, Any]:
        return validate_from_path(script_path)

    def list_all(self, project_dir: Any | None = None) -> dict[str, Any]:
        return list_all(project_dir=project_dir)
