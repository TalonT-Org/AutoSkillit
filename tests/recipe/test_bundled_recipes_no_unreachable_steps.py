"""Bundled-recipe regression guard: no unreachable steps in any bundled recipe."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import all_validated_recipe_names, builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _bundled_recipe_names() -> list[str]:
    names = sorted(all_validated_recipe_names(_PROJECT_ROOT))
    rd = builtin_recipes_dir()
    return [n for n in names if (rd / f"{n}.yaml").exists()]


@pytest.mark.parametrize("recipe_name", _bundled_recipe_names())
def test_bundled_recipe_has_no_unreachable_steps(recipe_name: str) -> None:
    """Every step in a bundled recipe must be reachable from the entry point."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    unreachable = [
        f for f in findings if f.rule == "unreachable-step" and f.severity >= Severity.WARNING
    ]
    assert unreachable == [], f"Recipe '{recipe_name}' has unreachable steps: " + ", ".join(
        f"{f.step_name} ({f.message})" for f in unreachable
    )
