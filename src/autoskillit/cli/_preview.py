"""Shared pre-launch preview: flow diagram + ingredient table display."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.recipe.loader import RecipeInfo


def _render_pre_launch_preview(
    recipe_name: str, parsed_recipe: object, recipes_dir: Path, project_dir: Path
) -> None:
    from autoskillit.cli.ui._ansi import diagram_to_terminal, ingredients_to_terminal
    from autoskillit.config import resolve_ingredient_defaults
    from autoskillit.recipe import build_ingredient_rows, load_recipe_diagram

    diagram = load_recipe_diagram(recipe_name, recipes_dir)
    if diagram:
        print(diagram_to_terminal(diagram))
        print()

    resolved = resolve_ingredient_defaults(project_dir)
    rows = build_ingredient_rows(parsed_recipe, resolved_defaults=resolved)
    if rows:
        print(ingredients_to_terminal(rows))


def show_cook_preview(
    recipe_name: str, parsed_recipe: object, recipes_dir: Path, project_dir: Path
) -> None:
    _render_pre_launch_preview(recipe_name, parsed_recipe, recipes_dir, project_dir)


def show_campaign_preview(
    recipe_name: str, parsed_recipe: object, recipes_dir: Path, project_dir: Path
) -> None:
    _render_pre_launch_preview(recipe_name, parsed_recipe, recipes_dir, project_dir)


def _pre_launch_campaign(
    campaign_name: str,
    parsed_recipe: object,
    match: RecipeInfo,
    project_dir: Path,
    *,
    is_resume: bool,
) -> tuple[str | None, bool]:
    """Get ingredients table; show preview + confirmation for new launches.

    Returns (itable, proceed). proceed=False means the user declined the launch.
    """
    from autoskillit.cli._prompts import _get_ingredients_table  # noqa: PLC0415

    if not is_resume:
        show_campaign_preview(campaign_name, parsed_recipe, match.path.parent, project_dir)

    itable = _get_ingredients_table(campaign_name, match, project_dir)
    if is_resume:
        return itable, True

    from autoskillit.cli.ui._timed_input import timed_prompt  # noqa: PLC0415

    confirm = timed_prompt(
        "Launch campaign? [Enter/n]", default="", timeout=120, label="autoskillit fleet campaign"
    )
    return itable, confirm.lower() not in ("n", "no")
