"""Recipe-tool formatters for the pretty_output split.

Hosts the per-tool formatters for ``load_recipe``, ``open_kitchen``, and
``list_recipes`` along with their field-coverage contracts. Stdlib-only at
runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from _fmt_primitives import (  # type: ignore[import-not-found]
    _CHECK_MARK,
    _CROSS_MARK,
    _WARN_MARK,
)

if TYPE_CHECKING:
    from autoskillit.recipe import ListRecipesResult, LoadRecipeResult


# Field coverage contract for _fmt_load_recipe ↔ LoadRecipeResult
_FMT_LOAD_RECIPE_RENDERED: frozenset[str] = frozenset(
    {
        "valid",
        "suggestions",
        "error",
        "content",
        "ingredients_table",
    }
)
_FMT_LOAD_RECIPE_SUPPRESSED: frozenset[str] = frozenset(
    {
        "greeting",  # delivered via positional CLI arg, not MCP response
        "diagram",  # user sees it in terminal preview; agent doesn't need it
        "kitchen_rules",  # already in the YAML content
        "requires_packs",  # internal field; used for skill gating, not display
    }
)

# Maps derived-display field name → source field name in LoadRecipeResult.
# When a derived field is present in a response, the formatter strips its
# corresponding source block from the source field to prevent duplicate display.
# All entries must map to "content" — only content-derived fields require
# ingredients-block stripping. Non-content source fields are not supported here.
#
# HOW TO USE: When adding a new field to _FMT_LOAD_RECIPE_RENDERED, ask:
#   "Is this field a re-rendering of content already in another RENDERED field?"
# If yes, add an entry here: {new_derived_field: "content"}.
# The augmented field coverage test will enforce this declaration.
_LOAD_RECIPE_CONTENT_DERIVED_FROM: dict[str, str] = {
    "ingredients_table": "content",  # GFM table derived from the ingredients: block in content
}


def _strip_yaml_ingredients_block(yaml_text: str) -> str:
    """Remove the top-level `ingredients:` block from YAML text.

    Called by _fmt_recipe_body() when ingredients_table is present, so the
    RECIPE block does not repeat the TABLE block. Operates line-by-line:
    drops the `ingredients:` key and all its indented children until a
    non-indented line signals the next top-level key. Preserves all other
    top-level keys (steps, kitchen_rules, description, etc.) unchanged.
    """
    lines = yaml_text.splitlines(keepends=True)
    result: list[str] = []
    in_ingredients = False
    for line in lines:
        if line.startswith("ingredients:"):
            in_ingredients = True
            continue
        if in_ingredients:
            if line and not line[0].isspace():
                # First non-indented non-empty line = next top-level key
                in_ingredients = False
                result.append(line)
            # else: still inside the ingredients block — skip
        else:
            result.append(line)
    return "".join(result)


def _fmt_recipe_body(data: Mapping[str, Any]) -> list[str]:
    """Shared recipe content rendering for load_recipe and open_kitchen+recipe."""
    lines: list[str] = []
    content = data.get("content")
    if content:
        # When a derived field is present, strip its source block from content
        # to prevent duplicate display. The derivation map drives this automatically.
        display_content = content
        for derived_field in _LOAD_RECIPE_CONTENT_DERIVED_FROM:
            if data.get(derived_field):
                display_content = _strip_yaml_ingredients_block(display_content)
        lines.append("\n--- RECIPE ---")
        lines.append(display_content)
        lines.append("--- END RECIPE ---")
    ing_table = data.get("ingredients_table")
    if ing_table:
        lines.append("\n--- INGREDIENTS TABLE (display this verbatim to the user) ---")
        lines.append(ing_table)
        lines.append("--- END TABLE ---")
    suggestions = data.get("suggestions") or []
    errors = [
        f for f in suggestions if isinstance(f, dict) and f.get("severity") in ("error", "warning")
    ]
    if errors:
        lines.append(f"\n{len(errors)} finding(s)")
    return lines


def _fmt_load_recipe(data: LoadRecipeResult, pipeline: bool) -> str:
    """Format load_recipe result as Markdown-KV."""
    if not isinstance(data, dict):
        return "## load_recipe\n\n_(unexpected response type)_"

    error = data.get("error")
    if error:
        return f"## load_recipe {_CROSS_MARK}\n\n**Error:** {error}"

    valid = data.get("valid", True)
    mark = _CHECK_MARK if valid else _CROSS_MARK
    lines: list[str] = [f"## load_recipe {mark}"]
    lines.extend(_fmt_recipe_body(data))
    return "\n".join(lines)


# Field coverage contract for _fmt_list_recipes ↔ ListRecipesResult
_FMT_LIST_RECIPES_RENDERED: frozenset[str] = frozenset(
    {
        "recipes",
        "count",
        "errors",
    }
)
_FMT_LIST_RECIPES_SUPPRESSED: frozenset[str] = frozenset()

# Field coverage contract for per-item recipe entries ↔ RecipeListItem
_FMT_RECIPE_LIST_ITEM_RENDERED: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "summary",
        "source",
    }
)
_FMT_RECIPE_LIST_ITEM_SUPPRESSED: frozenset[str] = frozenset()


def _fmt_open_kitchen(data: dict[str, Any], pipeline: bool) -> str:
    """Format open_kitchen combined kitchen+recipe result."""
    version = data.get("version", "")

    error = data.get("error")
    if error:
        return f"## open_kitchen {_CROSS_MARK} v{version}\n\nKitchen open. Recipe error: {error}"

    valid = data.get("valid", True)
    mark = _CHECK_MARK if valid else _CROSS_MARK
    lines: list[str] = [f"## open_kitchen {mark} v{version}"]
    lines.extend(_fmt_recipe_body(data))
    return "\n".join(lines)


def _fmt_open_kitchen_plain_text(text: str, _pipeline: bool) -> str:
    """Format open_kitchen plain-text response (no recipe attached)."""
    return f"## open_kitchen\n\n{text}"


def _fmt_list_recipes(data: ListRecipesResult, pipeline: bool) -> str:
    """Format list_recipes result as Markdown-KV."""
    if not isinstance(data, dict):
        return "## list_recipes\n\n_(unexpected response type)_"
    lines: list[str] = ["## list_recipes"]
    recipes = data.get("recipes") or []
    for recipe in recipes[:30]:
        if isinstance(recipe, dict):
            name = recipe.get("name", "?")
            desc = recipe.get("description", "")
            summary = recipe.get("summary", "")
            source = recipe.get("source", "")
            source_tag = f" [{source}]" if source else ""
            lines.append(f"  - {name}{source_tag}: {desc}" if desc else f"  - {name}{source_tag}")
            if summary:
                lines.append(f"    {summary}")
        else:
            lines.append(f"  - {recipe}")
    if len(recipes) > 30:
        lines.append(f"  ... and {len(recipes) - 30} more")
    count = data.get("count", len(recipes))
    lines.append(f"\n{count} recipe(s) available")
    errors = data.get("errors") or []
    if errors:
        lines.append(f"\n{_WARN_MARK} {len(errors)} recipe file(s) had load errors")
    return "\n".join(lines)
