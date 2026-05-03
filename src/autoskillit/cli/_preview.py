"""Shared pre-launch preview: flow diagram + ingredient table display."""

from __future__ import annotations

from pathlib import Path


def _render_pre_launch_preview(
    recipe_name: str, parsed_recipe: object, recipes_dir: Path, project_dir: Path
) -> None:
    from autoskillit.cli._ansi import diagram_to_terminal, ingredients_to_terminal
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
