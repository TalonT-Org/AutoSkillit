"""
Tests for the review-loop-waypoint-guard semantic rule.

The rule fires when check_repo_ci_event is BFS-reachable from review_pr
without crossing check_review_loop.
"""

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

REVIEW_LOOP_RECIPES = ["implementation", "remediation", "implementation-groups"]
RULE_ID = "review-loop-waypoint-guard"


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def test_waypoint_rule_is_registered():
    """The rule must be registered in the semantic rule registry."""
    rule_ids = {r.name for r in _RULE_REGISTRY}
    assert RULE_ID in rule_ids, (
        f"{RULE_ID} not found in rule registry. Registered rules: {sorted(rule_ids)}"
    )


@pytest.mark.parametrize("recipe_name", REVIEW_LOOP_RECIPES)
def test_waypoint_rule_silent_on_fixed_recipes(recipe_name):
    """
    After Part A fix (all verdicts route through check_review_loop), the rule
    must not fire on any of the three review-loop recipes.
    This is the primary regression guard.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) == 0, (
        f"[{recipe_name}] {RULE_ID} fired after Part A fix: {rule_violations}"
    )


def test_waypoint_rule_fires_on_recipe_with_bypass():
    """
    When a recipe has a direct review_pr → check_repo_ci_event route (the old
    bug pattern), the rule must fire with a descriptive violation.
    """
    recipe = _make_recipe(
        {
            "review_pr": RecipeStep(
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="check_repo_ci_event")]
                )
            ),
            "check_review_loop": RecipeStep(on_success="check_repo_ci_event"),
            "check_repo_ci_event": RecipeStep(),
        }
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) == 1
    assert "check_review_loop" in rule_violations[0].message
    assert "check_repo_ci_event" in rule_violations[0].message


def test_waypoint_rule_silent_when_all_verdicts_go_through_barrier():
    """
    When review_pr routes all verdicts through check_review_loop, rule must not fire.
    """
    recipe = _make_recipe(
        {
            "review_pr": RecipeStep(
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="check_review_loop")]
                )
            ),
            "check_review_loop": RecipeStep(
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="check_repo_ci_event")]
                )
            ),
            "check_repo_ci_event": RecipeStep(),
        }
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) == 0


def test_waypoint_rule_skips_recipes_without_review_loop_steps():
    """
    Recipes that don't have the review_pr / check_review_loop / check_repo_ci_event
    trio are not subject to this rule.
    """
    recipe = _make_recipe(
        {
            "merge_worktree": RecipeStep(tool="merge_worktree"),
            "push_to_remote": RecipeStep(tool="push_to_remote"),
        }
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) == 0


# ---------------------------------------------------------------------------
# review-mode-reentry-waypoint-guard tests
# ---------------------------------------------------------------------------

REENTRY_RULE_ID = "review-mode-reentry-waypoint-guard"


def test_reentry_waypoint_rule_is_registered():
    """The re-entry waypoint rule must be registered in the semantic rule registry."""
    rule_ids = {r.name for r in _RULE_REGISTRY}
    assert REENTRY_RULE_ID in rule_ids, (
        f"{REENTRY_RULE_ID} not found in rule registry. Registered rules: {sorted(rule_ids)}"
    )


@pytest.mark.parametrize("recipe_name", REVIEW_LOOP_RECIPES)
def test_reentry_waypoint_rule_silent_on_fixed_recipes(recipe_name):
    """
    After the routing fix (check_review_loop routes to annotate_pr_diff on loop re-entry),
    the re-entry waypoint rule must not fire on any of the three review-loop recipes.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == REENTRY_RULE_ID]
    assert len(rule_violations) == 0, (
        f"[{recipe_name}] {REENTRY_RULE_ID} fired after routing fix: {rule_violations}"
    )


def test_reentry_waypoint_rule_fires_on_recipe_with_bypass():
    """
    When a recipe has a direct check_review_loop → review_pr route (the bug pattern),
    the re-entry waypoint rule must fire with a descriptive violation.
    """
    recipe = _make_recipe(
        {
            "annotate_pr_diff": RecipeStep(tool="run_python", on_success="review_pr"),
            "review_pr": RecipeStep(
                tool="run_skill",
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="check_review_loop")]
                ),
            ),
            "check_review_loop": RecipeStep(
                tool="run_python",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.had_blocking }} == true",
                            route="review_pr",
                        ),
                        StepResultCondition(when=None, route="check_repo_ci_event"),
                    ]
                ),
            ),
            "check_repo_ci_event": RecipeStep(tool="check_repo_merge_state"),
        }
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == REENTRY_RULE_ID]
    assert len(rule_violations) == 1
    assert "annotate_pr_diff" in rule_violations[0].message
