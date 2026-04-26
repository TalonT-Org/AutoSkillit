"""Concrete RecipeRepository implementation."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from autoskillit.core import LoadResult
    from autoskillit.recipe.schema import Recipe, RecipeInfo

import autoskillit.recipe._api as _api
from autoskillit.recipe.contracts import StaleItem, load_bundled_manifest
from autoskillit.recipe.io import builtin_recipes_dir, list_recipes, load_recipe
from autoskillit.recipe.staleness_cache import (
    StalenessEntry,
    compute_recipe_hash,
    read_staleness_cache,
    write_staleness_cache,
)


def _dir_mtime(path: Path) -> float:
    """Return directory mtime as float, or 0.0 if the path does not exist."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class DefaultRecipeRepository:
    """Concrete RecipeRepository backed by list_recipes with in-memory mtime cache."""

    def __init__(self) -> None:
        self._cached_list: LoadResult[RecipeInfo] | None = None
        self._cached_project_dir: Path | None = None
        self._cached_project_mtime: float = 0.0
        self._cached_builtin_mtime: float = 0.0

    def _get_list(self, project_dir: Path) -> LoadResult[RecipeInfo]:
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

    def find(self, name: str, project_dir: Path) -> RecipeInfo | None:
        result = self._get_list(project_dir)
        return next((r for r in result.items if r.name == name), None)

    def load(self, path: Path) -> Recipe:
        """Load and parse the recipe at *path*.

        Exceptions raised by :func:`load_recipe` (e.g. ``FileNotFoundError``,
        ``yaml.YAMLError``, ``ValidationError``) are intentionally not wrapped —
        thin delegation is the contract. Callers that need a uniform error type
        should catch at the call site.
        """
        return load_recipe(path)

    def list(self, project_dir: Path) -> LoadResult[RecipeInfo]:
        return self._get_list(project_dir)

    def load_and_validate(
        self,
        name: str,
        project_dir: Any,
        *,
        suppressed: Sequence[str] | None = None,
        resolved_defaults: dict[str, str] | None = None,
        ingredient_overrides: dict[str, str] | None = None,
        temp_dir: Path | None = None,
        temp_dir_relpath: str | None = None,
    ) -> dict[str, Any]:
        recipe_info = self.find(name, project_dir)
        return cast(
            dict[str, Any],
            _api.load_and_validate(
                name,
                project_dir=project_dir,
                suppressed=suppressed,
                recipe_info=recipe_info,
                resolved_defaults=resolved_defaults,
                ingredient_overrides=ingredient_overrides,
                temp_dir=temp_dir,
                temp_dir_relpath=temp_dir_relpath,
            ),
        )

    def validate_from_path(
        self, script_path: Any, temp_dir_relpath: str = ".autoskillit/temp"
    ) -> dict[str, Any]:
        return _api.validate_from_path(script_path, temp_dir_relpath=temp_dir_relpath)

    def list_all(
        self,
        project_dir: Any | None = None,
        *,
        features: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        return _api.list_all(project_dir=project_dir, features=features)

    async def apply_triage_gate(
        self,
        result: dict[str, Any],
        recipe_name: str,
        recipe_info: Any,
        temp_dir: Path,
        logger: Any,
        triage_fn: Any = None,
    ) -> dict[str, Any]:
        """Apply LLM triage to stale-contract suggestions, suppressing cosmetic ones."""
        stale_suggs = [
            s for s in result.get("suggestions", []) if s.get("rule") == "stale-contract"
        ]
        if not stale_suggs:
            return result

        if recipe_info is None:
            recipe_info = self.find(recipe_name, Path.cwd())
        if recipe_info is None:
            return result

        cache_path = temp_dir / "recipe_staleness_cache.json"
        t0 = time.perf_counter()
        cached = read_staleness_cache(cache_path, recipe_name)
        logger.debug(
            "triage_gate_cache_read",
            recipe=recipe_name,
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        )

        if cached is not None and cached.triage_result == "cosmetic":
            result["suggestions"] = [
                s for s in result["suggestions"] if s.get("rule") != "stale-contract"
            ]
            return result

        if cached is None or cached.triage_result is None:
            hash_items = [
                StaleItem(
                    skill=s["skill"],
                    reason=s["reason"],
                    stored_value=s.get("stored_value", ""),
                    current_value=s.get("current_value", ""),
                )
                for s in stale_suggs
                if s.get("reason") == "hash_mismatch"
            ]
            if hash_items and triage_fn is not None:
                t_llm = time.perf_counter()
                triage = await triage_fn(hash_items)
                logger.debug(
                    "triage_gate_llm_triage",
                    recipe=recipe_name,
                    elapsed_ms=round((time.perf_counter() - t_llm) * 1000, 1),
                )
                if not triage:
                    return result
                all_cosmetic = all(not r.get("meaningful", True) for r in triage)
                triage_str = "cosmetic" if all_cosmetic else "meaningful"
                current_hash = compute_recipe_hash(recipe_info.path)
                current_ver = load_bundled_manifest().get("version", "")
                write_staleness_cache(
                    cache_path,
                    recipe_name,
                    StalenessEntry(
                        recipe_hash=current_hash,
                        manifest_version=current_ver,
                        is_stale=True,
                        triage_result=triage_str,
                        checked_at=datetime.now(UTC).isoformat(),
                    ),
                )
                if all_cosmetic and not any(
                    s.get("reason") == "version_mismatch" for s in stale_suggs
                ):
                    result["suggestions"] = [
                        s for s in result["suggestions"] if s.get("rule") != "stale-contract"
                    ]

        return result
