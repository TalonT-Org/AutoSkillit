"""Recipe I/O and parsing — load, list, and parse recipe YAML files."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from autoskillit.core import LoadReport, LoadResult, RecipeSource, get_logger, load_yaml, pkg_root
from autoskillit.recipe.schema import (
    AUTOSKILLIT_VERSION_KEY,
    Recipe,
    RecipeInfo,
    RecipeIngredient,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
    StepRetry,
)

logger = get_logger(__name__)

# Module-level mtime cache for list_recipes
_list_cache: dict[tuple, Any] = {}


def _dir_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def load_recipe(path: Path) -> Recipe:
    """Parse a YAML recipe file into a Recipe dataclass."""
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"Recipe file must contain a YAML mapping: {path}")
    return _parse_recipe(data)


def _load_from_disk(project_dir: Path) -> LoadResult[RecipeInfo]:
    """Load recipes from disk without any caching."""
    seen: set[str] = set()
    items: list[RecipeInfo] = []
    errors: list[LoadReport] = []

    project_recipe_dir = project_dir / ".autoskillit" / "recipes"
    _collect_recipes(RecipeSource.PROJECT, project_recipe_dir, seen, items, errors)

    builtin_dir = pkg_root() / "recipes"
    _collect_recipes(RecipeSource.BUILTIN, builtin_dir, seen, items, errors)

    return LoadResult(items=sorted(items, key=lambda r: r.name), errors=errors)


def list_recipes(project_dir: Path) -> LoadResult[RecipeInfo]:
    """Find available recipes from project and built-in sources.

    Results are cached by directory mtime to avoid redundant disk reads.
    """
    pm = _dir_mtime(project_dir / ".autoskillit" / "recipes")
    bm = _dir_mtime(pkg_root() / "recipes")
    cache_key = (project_dir, pm, bm)
    if cache_key in _list_cache:
        return _list_cache[cache_key]
    result = _load_from_disk(project_dir)
    _list_cache[cache_key] = result
    return result


def builtin_recipes_dir() -> Path:
    """Return the path to the built-in recipes directory."""
    return pkg_root() / "recipes"


def iter_steps_with_context(
    recipe: Recipe,
) -> Iterator[tuple[str, RecipeStep, frozenset[str]]]:
    """Yield (name, step, available_context) with accumulated captures."""
    available: set[str] = set()
    for step_name, step in recipe.steps.items():
        yield step_name, step, frozenset(available)
        if step.capture:
            available.update(step.capture.keys())
        if step.capture_list:
            available.update(step.capture_list.keys())


def find_recipe_by_name(name: str, project_dir: Path) -> RecipeInfo | None:
    """Find a recipe by name from project and built-in sources.

    Returns the first match (project takes precedence), or None if not found.
    """
    result = list_recipes(project_dir)
    return next((r for r in result.items if r.name == name), None)


# --- internal helpers ---


def _parse_recipe(data: dict[str, Any]) -> Recipe:
    name = data.get("name", "")
    description = data.get("description", "")
    summary = data.get("summary", "")

    ingredients: dict[str, RecipeIngredient] = {}
    for inp_name, inp_data in (data.get("ingredients") or {}).items():
        if isinstance(inp_data, dict):
            ingredients[inp_name] = RecipeIngredient(
                description=inp_data.get("description", ""),
                required=inp_data.get("required", False),
                default=inp_data.get("default"),
            )

    steps: dict[str, RecipeStep] = {}
    for step_name, step_data in (data.get("steps") or {}).items():
        if isinstance(step_data, dict):
            steps[step_name] = _parse_step(step_data)

    kitchen_rules = data.get("kitchen_rules", [])
    if not isinstance(kitchen_rules, list):
        kitchen_rules = []

    return Recipe(
        name=name,
        description=description,
        summary=summary,
        ingredients=ingredients,
        steps=steps,
        kitchen_rules=kitchen_rules,
        version=data.get(AUTOSKILLIT_VERSION_KEY),
    )


def _parse_retry(retry_data: dict[str, Any]) -> StepRetry:
    """Parse a retry block dict into a StepRetry instance."""
    # YAML 1.1 parsers (yaml.safe_load) interpret bare 'on' as boolean True.
    # Normalise: prefer string key "on", fall back to boolean True key.
    on_value = retry_data.get("on") if "on" in retry_data else retry_data.get(True)  # type: ignore[call-overload]
    return StepRetry(
        max_attempts=retry_data.get("max_attempts", 3),
        on=on_value,
        on_exhausted=retry_data.get("on_exhausted", "escalate"),
    )


def _parse_on_result(on_result_data: Any) -> StepResultRoute:
    """Parse an on_result list into a StepResultRoute instance."""
    if isinstance(on_result_data, dict):
        raise ValueError(
            "legacy on_result field+routes format removed in v0.3.0; "
            "use predicate conditions format (a YAML list). "
            "Run 'autoskillit migrate' to upgrade your recipe."
        )
    conditions = []
    if isinstance(on_result_data, list):
        for item in on_result_data:
            if isinstance(item, dict):
                conditions.append(
                    StepResultCondition(
                        when=item.get("when"),
                        route=item.get("route", ""),
                    )
                )
    return StepResultRoute(conditions=conditions)


def _parse_step(data: dict[str, Any]) -> RecipeStep:
    kwargs: dict[str, Any] = {}
    valid_fields = {f.name for f in dataclasses.fields(RecipeStep)}

    for key, value in data.items():
        field_name = "with_args" if key == "with" else key
        if field_name not in valid_fields:
            continue  # unknown YAML key — silently skip
        kwargs[field_name] = value

    # Special cases that need transformation:
    if "on_result" in kwargs:
        kwargs["on_result"] = _parse_on_result(kwargs["on_result"])
        if not kwargs["on_result"].conditions:
            kwargs["on_result"] = None
    if "retry" in kwargs and isinstance(kwargs["retry"], dict):
        kwargs["retry"] = _parse_retry(kwargs["retry"])
    if "optional" in kwargs:
        kwargs["optional"] = bool(kwargs["optional"])

    return RecipeStep(**kwargs)  # dataclass defaults apply for absent fields


def _collect_recipes(
    source: RecipeSource,
    directory: Path,
    seen: set[str],
    result: list[RecipeInfo],
    errors: list[LoadReport],
) -> None:
    if not directory.is_dir():
        return
    for f in sorted(directory.iterdir()):
        if f.suffix in (".yaml", ".yml") and f.is_file():
            try:
                raw = f.read_text(encoding="utf-8")
                data = load_yaml(raw)
                if not isinstance(data, dict):
                    raise ValueError("recipe must be a YAML mapping")
                recipe = _parse_recipe(data)
                if recipe.name and recipe.name not in seen:
                    seen.add(recipe.name)
                    result.append(
                        RecipeInfo(
                            name=recipe.name,
                            description=recipe.description,
                            source=source,
                            path=f,
                            summary=recipe.summary,
                            version=recipe.version,
                            content=raw,
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to load recipe file", path=str(f), error=str(exc))
                errors.append(LoadReport(path=f, error=str(exc)))
