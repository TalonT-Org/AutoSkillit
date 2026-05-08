"""Ingredient formatting and response TypedDicts for recipe API."""

from __future__ import annotations

from typing import Any, TypedDict

from autoskillit.core import (
    TerminalColumn,
    _render_gfm_table,
)

# ---------------------------------------------------------------------------
# GFM ingredient table column specs
# ---------------------------------------------------------------------------

_GFM_DESC_MAX_WIDTH: int = 60

_GFM_INGREDIENT_COLUMNS: tuple[TerminalColumn, ...] = (
    TerminalColumn("Name", max_width=30, align=">"),
    TerminalColumn("Description", max_width=_GFM_DESC_MAX_WIDTH, align="<"),
    TerminalColumn("Default", max_width=20, align=">"),
)


# ---------------------------------------------------------------------------
# Schema contract: handler → formatter boundary
# ---------------------------------------------------------------------------


def _ingredient_sort_key(name: str, required: bool, default: object) -> tuple[int, str]:
    """Sort ingredients: required > auto-detect > flags > optional > constants."""
    if required and default is None:
        return (0, name)
    if default == "":
        return (1, name)
    if default in ("true", "false"):
        return (2, name)
    if default is None:
        return (3, name)
    return (4, name)  # has a non-empty default (constants, rarely changed)


def build_ingredient_rows(
    recipe: Any,
    resolved_defaults: dict[str, str] | None = None,
) -> list[tuple[str, str, str]]:
    """Build (name, description, default) rows for a recipe's ingredients.

    This is the shared source of truth for ingredient row data, consumed by
    both the GFM table formatter (LLM path) and the terminal renderer
    (terminal path). Descriptions are full-length here — truncation is the
    terminal renderer's responsibility.

    Returns rows sorted by ingredient priority (required first, then alphabetical).
    """
    resolved = resolved_defaults or {}
    raw: list[tuple[str, str, str, tuple[int, str]]] = []
    for name, ing in (getattr(recipe, "ingredients", None) or {}).items():
        if getattr(ing, "hidden", False):
            continue
        desc = getattr(ing, "description", "")
        required = getattr(ing, "required", False)
        default = getattr(ing, "default", None)
        sort_key = _ingredient_sort_key(name, required, default)
        if default is None and required:
            default_str, name_str = "(required)", f"{name} *"
        elif res := resolved.get(name):
            default_str, name_str = res, name
        elif default == "":
            default_str, name_str = "auto-detect", name
        elif default == "true":
            default_str, name_str = "on", name
        elif default == "false":
            default_str, name_str = "off", name
        elif default is None:
            default_str, name_str = "--", name
        else:
            default_str, name_str = str(default), name
        raw.append((name_str, desc, default_str, sort_key))
    raw.sort(key=lambda r: r[3])
    return [(r[0], r[1], r[2]) for r in raw]


def format_ingredients_table(
    recipe: Any, resolved_defaults: dict[str, str] | None = None
) -> str | None:
    """Build a pre-formatted ingredients table from a parsed Recipe.

    When ``resolved_defaults`` is provided, auto-detect ingredients (``default: ""``)
    use the resolved value instead of showing "auto-detect".
    """
    ingredients = getattr(recipe, "ingredients", None)
    if not ingredients:
        return None

    rows = build_ingredient_rows(recipe, resolved_defaults)

    if not rows:
        return None

    return _render_gfm_table(_GFM_INGREDIENT_COLUMNS, rows)


class LoadRecipeResult(TypedDict, total=False):
    """Typed schema for the load_recipe handler → formatter boundary."""

    content: str
    diagram: str | None
    suggestions: list[dict[str, Any]]
    valid: bool
    kitchen_rules: list[str]
    requires_packs: list[str]
    requires_features: list[str]
    error: str
    greeting: str
    ingredients_table: str
    orchestration_rules: str
    stop_step_semantics: str
    content_hash: str
    composite_hash: str
    recipe_version: str | None


class OpenKitchenResult(TypedDict, total=False):
    """Typed schema for the open_kitchen named-recipe handler → formatter boundary.

    Extends LoadRecipeResult with three post-return keys injected by the handler.
    """

    # Inherited from LoadRecipeResult (15 keys)
    content: str
    diagram: str | None
    suggestions: list[dict[str, Any]]
    valid: bool
    kitchen_rules: list[str]
    requires_packs: list[str]
    requires_features: list[str]
    error: str
    greeting: str
    ingredients_table: str | None
    orchestration_rules: str
    stop_step_semantics: str
    content_hash: str
    composite_hash: str
    recipe_version: str | None
    # Post-return keys injected by open_kitchen handler (4 keys)
    success: bool
    kitchen: str
    version: str
    hook_warning: str


class RecipeListItem(TypedDict):
    """Typed schema for a single recipe entry in the list_recipes response."""

    name: str
    description: str
    summary: str
    source: str


class ListRecipesResult(TypedDict, total=False):
    """Typed schema for the list_recipes handler → formatter boundary."""

    recipes: list[RecipeListItem]
    count: int
    errors: list[dict[str, str]]
