"""Recipe I/O and parsing — load, list, and parse recipe YAML files."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from autoskillit.core.io import load_yaml
from autoskillit.core.logging import get_logger
from autoskillit.core.types import LoadReport, LoadResult, RecipeSource
from autoskillit.recipe.schema import (
    AUTOSKILLIT_VERSION_KEY,
    Recipe,
    RecipeInfo,
    RecipeIngredient,
    RecipeStep,
    StepResultRoute,
    StepRetry,
)

logger = get_logger(__name__)


def load_recipe(path: Path) -> Recipe:
    """Parse a YAML recipe file into a Recipe dataclass."""
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"Recipe file must contain a YAML mapping: {path}")
    return _parse_recipe(data)


def list_recipes(project_dir: Path) -> LoadResult[RecipeInfo]:
    """Find available recipes from project and built-in sources."""
    seen: set[str] = set()
    items: list[RecipeInfo] = []
    errors: list[LoadReport] = []

    project_recipe_dir = project_dir / ".autoskillit" / "recipes"
    _collect_recipes(RecipeSource.PROJECT, project_recipe_dir, seen, items, errors)

    builtin_dir = Path(__file__).parent.parent / "recipes"
    _collect_recipes(RecipeSource.BUILTIN, builtin_dir, seen, items, errors)

    return LoadResult(items=sorted(items, key=lambda r: r.name), errors=errors)


def builtin_recipes_dir() -> Path:
    """Return the path to the built-in recipes directory."""
    return Path(__file__).parent.parent / "recipes"


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


def format_recipe_list_response(result: LoadResult[RecipeInfo]) -> dict[str, object]:
    """Build the MCP response dict for the list_recipes tool."""
    response: dict[str, object] = {
        "recipes": [
            {"name": r.name, "description": r.description, "summary": r.summary}
            for r in result.items
        ],
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return response


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


def _parse_step(data: dict[str, Any]) -> RecipeStep:
    retry = None
    retry_data = data.get("retry")
    if isinstance(retry_data, dict):
        retry = StepRetry(
            max_attempts=retry_data.get("max_attempts", 3),
            on=retry_data.get("on"),
            on_exhausted=retry_data.get("on_exhausted", "escalate"),
        )

    on_result = None
    on_result_data = data.get("on_result")
    if isinstance(on_result_data, dict):
        on_result = StepResultRoute(
            field=on_result_data.get("field", ""),
            routes=on_result_data.get("routes", {}),
        )

    return RecipeStep(
        tool=data.get("tool"),
        action=data.get("action"),
        python=data.get("python"),
        with_args=data.get("with", {}),
        on_success=data.get("on_success"),
        on_failure=data.get("on_failure"),
        on_result=on_result,
        retry=retry,
        message=data.get("message"),
        note=data.get("note"),
        capture=data.get("capture", {}),
        capture_list=data.get("capture_list", {}),
        optional=bool(data.get("optional", False)),
        model=data.get("model"),
    )


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
                recipe = load_recipe(f)
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
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to load recipe file", path=str(f), error=str(exc))
                errors.append(LoadReport(path=f, error=str(exc)))


class DefaultRecipeRepository:
    """Concrete RecipeRepository backed by find_recipe_by_name and list_recipes."""

    def find(self, name: str, project_dir: Path) -> Any:
        return find_recipe_by_name(name, project_dir)

    def list(self, project_dir: Path) -> Any:
        return list_recipes(project_dir)
