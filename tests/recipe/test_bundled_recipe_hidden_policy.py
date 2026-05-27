"""Parameterized CI gate: no pipeline-internal ingredient violations in bundled recipes."""

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

BUNDLED_RECIPE_NAMES = [
    "implementation",
    "remediation",
    "implementation-groups",
    "merge-prs",
    "full-audit",
]


@pytest.mark.parametrize("recipe_name", BUNDLED_RECIPE_NAMES)
def test_bundled_recipe_no_pipeline_internal_violations(recipe_name: str) -> None:
    """All bundled recipes must declare hidden: true on pipeline-internal ingredients.

    This test runs the pipeline-internal-not-hidden semantic rule against each recipe.
    Any ingredient whose description signals pipeline-internal use but lacks hidden: true
    causes this test to fail, providing a CI gate for future ingredient additions.
    """
    recipe_path = pkg_root() / "recipes" / f"{recipe_name}.yaml"
    recipe = load_recipe(recipe_path)
    all_findings = run_semantic_rules(recipe)
    violations = [f for f in all_findings if f.rule == "pipeline-internal-not-hidden"]
    assert violations == [], (
        f"Recipe '{recipe_name}' has pipeline-internal ingredients missing 'hidden: true':\n"
        + "\n".join(f"  - {v.step_name}: {v.message}" for v in violations)
    )


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "remediation",
        "implementation-groups",
    ],
)
def test_upfront_claimed_is_hidden_in_recipe(recipe_name: str) -> None:
    """upfront_claimed must have hidden: true in each affected recipe."""
    recipe = load_recipe(pkg_root() / "recipes" / f"{recipe_name}.yaml")
    ing = recipe.ingredients.get("upfront_claimed")
    assert ing is not None, f"upfront_claimed not found in {recipe_name}"
    assert ing.hidden is True, (
        f"upfront_claimed.hidden must be True in {recipe_name} "
        f"(it is set by process-issues, not by users)"
    )


@pytest.mark.parametrize("recipe_name", BUNDLED_RECIPE_NAMES)
def test_no_bundled_recipe_has_skip_when_false_on_hidden_ingredient(recipe_name: str) -> None:
    """No bundled recipe step may use skip_when_false referencing a hidden ingredient.

    This regression test catches any future recipe that introduces the pattern of
    skip_when_false pointing at a hidden: true ingredient, which would have been
    silently broken before server-side evaluation was added.
    """
    recipe_path = pkg_root() / "recipes" / f"{recipe_name}.yaml"
    recipe = load_recipe(recipe_path)
    violations = []
    for step_name, step in recipe.steps.items():
        if not step.skip_when_false:
            continue
        ref = step.skip_when_false
        if not ref.startswith("inputs."):
            continue
        ingredient_name = ref[len("inputs.") :]
        ing = recipe.ingredients.get(ingredient_name)
        if ing is not None and ing.hidden:
            msg = f"Step '{step_name}': skip_when_false refs hidden '{ingredient_name}'"
            violations.append(msg)
    assert violations == [], (
        f"Recipe '{recipe_name}' has skip_when_false on hidden ingredients:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
