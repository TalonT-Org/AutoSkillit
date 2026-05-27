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
def test_bundled_recipe_content_has_no_unresolved_hidden_skip_guards(recipe_name: str) -> None:
    """load_and_validate must not deliver unresolved hidden ingredient refs to the LLM.

    Regression guard: if server-side skip_when_false evaluation is accidentally
    removed or bypassed, this test will catch it by finding inputs.* references
    in skip_when_false fields of the served content.
    """
    from autoskillit.recipe import load_and_validate

    result = load_and_validate(recipe_name)
    recipe_content = result["content"]
    # Parse the raw recipe to find hidden ingredients referenced by skip_when_false
    recipe_obj = load_recipe(pkg_root() / "recipes" / f"{recipe_name}.yaml")
    hidden_ing_names = {name for name, ing in recipe_obj.ingredients.items() if ing.hidden}
    unresolved = []
    for ing_name in hidden_ing_names:
        ref = f"skip_when_false: inputs.{ing_name}"
        if ref in recipe_content:
            unresolved.append(ref)
    assert unresolved == [], (
        f"Recipe '{recipe_name}' content still has unresolved hidden ingredient refs "
        f"in skip_when_false after load_and_validate: {unresolved}. "
        f"Server-side evaluation may be broken."
    )
