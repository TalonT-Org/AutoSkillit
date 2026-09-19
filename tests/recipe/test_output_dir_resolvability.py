"""Verify recipe output_dir values are server-resolvable after serve-time substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe import load_recipe
from autoskillit.recipe.repository import DefaultRecipeRepository

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

RELATIVE_OUTPUT_DIR_RECIPES = [
    "implementation",
    "implementation-groups",
    "remediation",
    "merge-prs",
    "research",
    "research-design",
    "research-implement",
    "research-review",
    "promote-to-main-wrapper",
    "implement-findings",
]

ABSOLUTE_OUTPUT_DIR_RECIPES = ["planner"]


@pytest.mark.parametrize("recipe_name", RELATIVE_OUTPUT_DIR_RECIPES)
def test_output_dir_values_are_server_resolvable(recipe_name: str) -> None:
    """Every output_dir in with_args must NOT contain ${{ }} after serve-time substitution."""
    repo = DefaultRecipeRepository()
    recipe_info = repo.find(recipe_name, Path.cwd())
    assert recipe_info is not None, f"Recipe {recipe_name} not found"
    recipe = load_recipe(recipe_info.path)
    for step_name, step in recipe.steps.items():
        if "output_dir" in step.with_args:
            output_dir = step.with_args["output_dir"]
            if "${{ context." in output_dir:
                continue
            assert "${{" not in output_dir, (
                f"Recipe '{recipe_name}', step '{step_name}': output_dir "
                f"'{output_dir}' contains unresolvable ${{{{ }}}} template. "
                f"Use a relative path (e.g., '.autoskillit/temp/skill-name') instead."
            )


@pytest.mark.parametrize("recipe_name", ABSOLUTE_OUTPUT_DIR_RECIPES)
def test_absolute_output_dir_recipes_have_template_refs(recipe_name: str) -> None:
    """Absolute-path recipes MUST have ${{ }} in output_dir — they rely on LLM cooperation."""
    repo = DefaultRecipeRepository()
    recipe_info = repo.find(recipe_name, Path.cwd())
    assert recipe_info is not None
    recipe = load_recipe(recipe_info.path)
    has_output_dir = False
    for step_name, step in recipe.steps.items():
        if "output_dir" in step.with_args:
            has_output_dir = True
            output_dir = step.with_args["output_dir"]
            assert "${{" in output_dir, (
                f"Recipe '{recipe_name}', step '{step_name}': output_dir "
                f"'{output_dir}' is NOT a ${{{{ }}}} template but recipe is in "
                f"ABSOLUTE_OUTPUT_DIR_RECIPES — update the test categorization."
            )
    assert has_output_dir, f"Recipe '{recipe_name}' has no output_dir in with_args"
