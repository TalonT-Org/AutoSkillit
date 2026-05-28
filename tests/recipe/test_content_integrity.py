"""Tests for content integrity of served recipe YAML after skip guard resolution."""

from __future__ import annotations

import re

import pytest

from autoskillit.core import pkg_root
from autoskillit.recipe import load_and_validate
from autoskillit.recipe._recipe_composition import _step_block_pattern
from autoskillit.recipe.io import load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

BUNDLED_RECIPE_NAMES = [
    "implementation",
    "remediation",
    "implementation-groups",
    "merge-prs",
    "full-audit",
]


def _guarded_ingredients(recipe_name: str) -> list[tuple[str, list[str]]]:
    """Return list of (ingredient_name, [guarded_step_names]) for all skip_when_false guards."""
    recipe_obj = load_recipe(pkg_root() / "recipes" / f"{recipe_name}.yaml")
    result = []
    for ing_name in recipe_obj.ingredients:
        guarded = [
            step_name
            for step_name, step in recipe_obj.steps.items()
            if step.skip_when_false == f"inputs.{ing_name}"
        ]
        if guarded:
            result.append((ing_name, guarded))
    return result


def _make_params() -> list[tuple[str, str, list[str]]]:
    params = []
    for recipe_name in BUNDLED_RECIPE_NAMES:
        for ing_name, guarded_steps in _guarded_ingredients(recipe_name):
            params.append((recipe_name, ing_name, guarded_steps))
    return params


@pytest.mark.parametrize("recipe_name,ing_name,guarded_steps", _make_params())
def test_truthy_resolved_step_has_no_optional_signal_in_content(
    recipe_name: str, ing_name: str, guarded_steps: list[str]
) -> None:
    """After truthy resolution, no guarded step block retains optional: true or skip_when_false."""
    result = load_and_validate(recipe_name, ingredient_overrides={ing_name: "true"})
    assert result["valid"], f"load_and_validate failed: {result.get('suggestions')}"
    content = result["content"]
    residual_optional = []
    residual_skip = []
    for step_name in guarded_steps:
        block_match = re.search(
            rf"(?m){_step_block_pattern(re.escape(step_name))}",
            content,
        )
        if block_match is None:
            continue  # step removed entirely (falsy guard via default) — skip
        step_block = block_match.group(0)
        if re.search(r"^\s+optional:\s+true\s*$", step_block, re.MULTILINE | re.IGNORECASE):
            residual_optional.append(step_name)
        if re.search(
            rf"^\s+skip_when_false:\s+inputs\.{re.escape(ing_name)}\s*$",
            step_block,
            re.MULTILINE,
        ):
            residual_skip.append(step_name)
    assert residual_optional == [], (
        f"Recipe '{recipe_name}' ingredient '{ing_name}': steps {residual_optional} "
        f"retain 'optional: true' in served content after truthy resolution"
    )
    assert residual_skip == [], (
        f"Recipe '{recipe_name}' ingredient '{ing_name}': steps {residual_skip} "
        f"retain 'skip_when_false: inputs.{ing_name}' in served content after truthy resolution"
    )
