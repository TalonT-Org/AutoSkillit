"""Recipe I/O and parsing — load, list, and parse recipe YAML files."""

from __future__ import annotations

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
)

logger = get_logger(__name__)


_TEMP_PLACEHOLDER = "{{AUTOSKILLIT_TEMP}}"


def substitute_temp_placeholder(text: str, temp_dir_relpath: str) -> str:
    """Replace ``{{AUTOSKILLIT_TEMP}}`` in raw recipe/skill text.

    Validates that ``temp_dir_relpath`` is YAML-safe (no newlines or
    ``": "`` sequences); raises ``ValueError`` otherwise. Filesystem paths
    should never contain these characters, but the guard makes the failure
    loud and free.
    """
    if "\n" in temp_dir_relpath or ": " in temp_dir_relpath:
        raise ValueError(f"temp_dir_relpath is YAML-unsafe: {temp_dir_relpath!r}")
    return text.replace(_TEMP_PLACEHOLDER, temp_dir_relpath)


def load_recipe(path: Path, temp_dir_relpath: str = ".autoskillit/temp") -> Recipe:
    """Parse a YAML recipe file into a Recipe dataclass.

    Substitutes ``{{AUTOSKILLIT_TEMP}}`` in the raw text with
    ``temp_dir_relpath`` *before* YAML parsing so the resulting Recipe
    dataclass observes the resolved value uniformly.
    """
    raw_text = path.read_text(encoding="utf-8")
    substituted = substitute_temp_placeholder(raw_text, temp_dir_relpath)
    data = load_yaml(substituted)
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

    builtin_dir = pkg_root() / "recipes"
    _collect_recipes(RecipeSource.BUILTIN, builtin_dir, seen, items, errors)

    return LoadResult(
        items=sorted(items, key=lambda r: (r.source != RecipeSource.BUILTIN, r.name)),
        errors=errors,
    )


def builtin_recipes_dir() -> Path:
    """Return the path to the built-in recipes directory."""
    return pkg_root() / "recipes"


def builtin_sub_recipes_dir() -> Path:
    """Return the path to the built-in sub-recipes directory."""
    return pkg_root() / "recipes" / "sub-recipes"


def find_sub_recipe_by_name(name: str, project_dir: Path) -> Path | None:
    """Find a sub-recipe YAML file by name.

    Searches project-local sub-recipes first (project takes precedence),
    then falls back to built-in sub-recipes.
    Sub-recipe files are stored in {dir}/sub-recipes/{name}.yaml and are
    NOT listed by list_recipes() (they are implementation details, not
    user-facing recipes).
    """
    project_sub_dir = project_dir / ".autoskillit" / "recipes" / "sub-recipes"
    for directory in [project_sub_dir, builtin_sub_recipes_dir()]:
        candidate = directory / f"{name}.yaml"
        if candidate.is_file():
            return candidate
    return None


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

# Fields explicitly handled by _parse_step. Must match RecipeStep.__dataclass_fields__
# exactly. When a field is added to RecipeStep, add handling in _parse_step AND add the
# field name here — the assertion below will fail at import time otherwise.
_PARSE_STEP_HANDLED_FIELDS: frozenset[str] = frozenset(
    {
        "tool",
        "action",
        "python",
        "constant",
        "with_args",
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_result",
        "retries",
        "on_exhausted",
        "message",
        "note",
        "capture",
        "capture_list",
        "optional",
        "skip_when_false",
        "model",
        "description",
        "sub_recipe",
        "gate",
        "optional_context_refs",
        "stale_threshold",
        "idle_output_timeout",
    }
)
if _PARSE_STEP_HANDLED_FIELDS != frozenset(RecipeStep.__dataclass_fields__):
    raise RuntimeError(
        "_parse_step field list is out of sync with RecipeStep schema.\n"
        f"  Missing from handled: "
        f"{frozenset(RecipeStep.__dataclass_fields__) - _PARSE_STEP_HANDLED_FIELDS}\n"
        f"  Extra in handled:     "
        f"{_PARSE_STEP_HANDLED_FIELDS - frozenset(RecipeStep.__dataclass_fields__)}"
    )


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
                hidden=bool(inp_data.get("hidden", False)),
            )

    steps: dict[str, RecipeStep] = {}
    for step_name, step_data in (data.get("steps") or {}).items():
        if isinstance(step_data, dict):
            steps[step_name] = _parse_step(step_data)

    kitchen_rules = data.get("kitchen_rules", [])
    if not isinstance(kitchen_rules, list):
        raise ValueError(f"'kitchen_rules' must be a list, got {type(kitchen_rules).__name__!r}")

    requires_packs_raw = data.get("requires_packs") or []
    if not isinstance(requires_packs_raw, list):
        raise ValueError(
            f"'requires_packs' must be a list, got {type(requires_packs_raw).__name__!r}"
        )

    return Recipe(
        name=name,
        description=description,
        summary=summary,
        ingredients=ingredients,
        steps=steps,
        kitchen_rules=kitchen_rules,
        version=data.get(AUTOSKILLIT_VERSION_KEY),
        experimental=bool(data.get("experimental", False)),
        requires_packs=requires_packs_raw,
    )


def _parse_step(data: dict[str, Any]) -> RecipeStep:
    if "retry" in data:
        raise ValueError(
            "The 'retry:' block is no longer supported. "
            "Use flat step-level fields: 'retries', 'on_exhausted', 'on_context_limit'."
        )

    on_result = None
    on_result_data = data.get("on_result")
    if isinstance(on_result_data, dict):
        on_result = StepResultRoute(
            field=on_result_data.get("field", ""),
            routes=on_result_data.get("routes", {}),
        )
    elif isinstance(on_result_data, list):
        conditions = []
        for item in on_result_data:
            if isinstance(item, dict):
                conditions.append(
                    StepResultCondition(
                        when=item.get("when"),
                        route=item.get("route", ""),
                    )
                )
        if conditions:
            on_result = StepResultRoute(conditions=conditions)

    return RecipeStep(
        tool=data.get("tool"),
        action=data.get("action"),
        python=data.get("python"),
        constant=data.get("constant"),
        with_args=data.get("with", {}),
        on_success=data.get("on_success"),
        on_failure=data.get("on_failure"),
        on_context_limit=data.get("on_context_limit"),
        on_result=on_result,
        retries=data.get("retries", 3),
        on_exhausted=data.get("on_exhausted", "escalate"),
        message=data.get("message"),
        note=data.get("note"),
        capture=data.get("capture", {}),
        capture_list=data.get("capture_list", {}),
        optional=bool(data.get("optional", False)),
        skip_when_false=data.get("skip_when_false"),
        model=data.get("model"),
        description=data.get("description", ""),
        sub_recipe=data.get("sub_recipe"),
        gate=data.get("gate"),
        optional_context_refs=data.get("optional_context_refs", []),
        stale_threshold=data.get("stale_threshold"),
        idle_output_timeout=data.get("idle_output_timeout"),
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
