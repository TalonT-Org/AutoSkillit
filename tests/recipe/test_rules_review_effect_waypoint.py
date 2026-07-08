"""Tests for the review-effect-verification-waypoint semantic rule.

The rule fires when any advance gate (check_review_loop or derive_batch_ci_event)
is BFS-reachable from a review-pr skill step without crossing check_review_posted.
"""

from __future__ import annotations

import pytest

from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules
from autoskillit.recipe.rules.graph.rules_graph_review import _get_review_pr_steps
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RULE_ID = "review-effect-verification-waypoint"
REVIEW_LOOP_RECIPES = ["implementation", "remediation", "implementation-groups"]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def test_effect_waypoint_rule_is_registered():
    """The rule must be registered in the semantic rule registry."""
    rule_ids = {r.name for r in _RULE_REGISTRY}
    assert RULE_ID in rule_ids, (
        f"{RULE_ID} not found in rule registry. Registered rules: {sorted(rule_ids)}"
    )


@pytest.mark.parametrize("recipe_name", REVIEW_LOOP_RECIPES)
def test_effect_waypoint_rule_silent_on_fixed_recipes(recipe_name):
    """After Part A fix, the rule must not fire on review-loop recipes."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    selected = _get_review_pr_steps(make_validation_context(recipe))
    assert len(selected) >= 1, (
        f"precondition: _get_review_pr_steps returned no steps for {recipe_name!r} — "
        "rule would be vacuously silent (dead matcher?)"
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) == 0, (
        f"[{recipe_name}] {RULE_ID} fired after fix: {rule_violations}"
    )


def test_effect_waypoint_rule_silent_on_merge_prs():
    """After Part A fix, the rule must not fire on merge-prs.yaml."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    selected = _get_review_pr_steps(make_validation_context(recipe))
    assert len(selected) >= 1, (
        "precondition: _get_review_pr_steps returned no steps for 'merge-prs' — "
        "rule would be vacuously silent (dead matcher?)"
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) == 0, f"[merge-prs] {RULE_ID} fired after fix: {rule_violations}"


def test_effect_waypoint_rule_fires_when_check_review_loop_reachable_without_gate():
    """T-B-RULE1: Rule fires when check_review_loop reachable without check_review_posted."""
    recipe = _make_recipe(
        {
            "review_pr": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr main feature"},
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="check_review_loop")]
                ),
            ),
            "check_review_loop": RecipeStep(
                tool="run_python",
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="check_repo_ci_event")]
                ),
            ),
            "check_repo_ci_event": RecipeStep(),
        }
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) >= 1
    assert all(v.severity.name == "ERROR" for v in rule_violations)
    assert any("check_review_loop" in v.message for v in rule_violations)


def test_effect_waypoint_rule_fires_when_derive_batch_ci_event_reachable_without_gate():
    """T-B-RULE1: Rule fires when derive_batch_ci_event reachable without check_review_posted."""
    recipe = _make_recipe(
        {
            "review_pr_integration": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr main feature"},
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="derive_batch_ci_event")]
                ),
            ),
            "derive_batch_ci_event": RecipeStep(tool="check_repo_merge_state"),
        }
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) >= 1
    assert all(v.severity.name == "ERROR" for v in rule_violations)
    assert any("derive_batch_ci_event" in v.message for v in rule_violations)


def test_effect_waypoint_rule_silent_when_gate_present():
    """T-B-RULE1: Rule silent when check_review_posted is barrier before check_review_loop."""
    recipe = _make_recipe(
        {
            "review_pr": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr main feature"},
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="check_review_posted")]
                ),
            ),
            "check_review_posted": RecipeStep(
                tool="run_python",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.reviews_posted }} == 'true'",
                            route="check_review_loop",
                        ),
                        StepResultCondition(when=None, route="failure_step"),
                    ]
                ),
            ),
            "check_review_loop": RecipeStep(on_success="done"),
            "failure_step": RecipeStep(action="stop", message="fail"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    selected = _get_review_pr_steps(make_validation_context(recipe))
    assert len(selected) >= 1, (
        "precondition: _get_review_pr_steps returned no steps — "
        "rule would be vacuously silent (dead matcher?)"
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) == 0


def test_effect_waypoint_rule_skips_recipes_without_review_pr_skill_steps():
    """Recipes without run_skill steps dispatching review-pr are not subject to this rule."""
    recipe = _make_recipe(
        {
            "merge_worktree": RecipeStep(tool="merge_worktree"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) == 0


def test_effect_waypoint_rule_covers_aliased_step_names():
    """Rule fires for review_pr_integration (aliased step id) dispatching review-pr skill."""
    recipe = _make_recipe(
        {
            "review_pr_integration": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-pr batch main"},
                on_result=StepResultRoute(
                    conditions=[StepResultCondition(when="true", route="check_review_loop")]
                ),
            ),
            "check_review_loop": RecipeStep(on_success="done"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    violations = run_semantic_rules(recipe)
    rule_violations = [v for v in violations if v.rule == RULE_ID]
    assert len(rule_violations) >= 1


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "implementation-groups",
        "remediation",
        "merge-prs",
    ],
)
def test_review_effect_waypoint_selects_steps_on_bundled_recipes(recipe_name):
    """Step-selector must return >= 1 steps on each bundled recipe.

    This test fails immediately if the rule's step-selection predicate
    is wrong (dead matcher) — before any silence test can pass vacuously.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    ctx = make_validation_context(recipe)
    selected = _get_review_pr_steps(ctx)
    assert len(selected) >= 1, (
        f"_get_review_pr_steps returned no steps for {recipe_name!r} — "
        "the rule is inactive on this recipe"
    )
