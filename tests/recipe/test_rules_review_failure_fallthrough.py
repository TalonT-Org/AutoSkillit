"""
Tests for the review-failure-fallthrough-guard semantic rule (#4391 criterion 2).

The rule fires when a CI-advance gate (check_review_loop, check_repo_ci_event,
derive_batch_ci_event) is BFS-reachable from a review-family step's
failure-shaped edges (on_failure, on_context_limit, on_rate_limit,
on_exhausted) without first crossing check_review_posted or re-entering a
review-family step. #1684 shipped exactly that fall-through for three
months; #4448 reversed it with nothing preventing a re-flip.
"""

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules
from autoskillit.recipe.rules.graph.rules_graph_review import _get_review_family_steps
from autoskillit.recipe.schema import Recipe, RecipeStep
from tests.recipe.conftest import BUNDLED_RECIPE_NAMES, assert_no_rule_errors

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RULE_ID = "review-failure-fallthrough-guard"
REVIEW_FAMILY_RECIPE_NAMES = [
    "implementation",
    "remediation",
    "implementation-groups",
    "merge-prs",
]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _review_pr_step(**kwargs) -> RecipeStep:
    return RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:review-pr"},
        **kwargs,
    )


def _resolve_review_step(**kwargs) -> RecipeStep:
    return RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:resolve-review"},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# T8.1 — registration
# ---------------------------------------------------------------------------


def test_rule_is_registered():
    """The rule must be registered in the semantic rule registry."""
    rule_ids = {r.name for r in _RULE_REGISTRY}
    assert RULE_ID in rule_ids, (
        f"{RULE_ID} not found in rule registry. Registered rules: {sorted(rule_ids)}"
    )


# ---------------------------------------------------------------------------
# T8.2 — silence on all bundled recipes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_name", BUNDLED_RECIPE_NAMES)
def test_rule_silent_on_bundled_recipes(recipe_name):
    """The rule must not fire on any bundled recipe — the routing shape #4448
    established (and this rule guards) is already the shipped shape."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    assert_no_rule_errors(findings, context=recipe_name)


@pytest.mark.parametrize("recipe_name", REVIEW_FAMILY_RECIPE_NAMES)
def test_rule_is_exercised_not_vacuously_silent(recipe_name):
    """Proves the silence in test_rule_silent_on_bundled_recipes is earned —
    each of these recipes actually has review-family steps for the rule to
    check, not an empty early-return."""
    from autoskillit.recipe._analysis import make_validation_context

    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    ctx = make_validation_context(recipe)
    review_steps = _get_review_family_steps(ctx)
    assert review_steps, f"[{recipe_name}] expected review-family steps, found none"


# ---------------------------------------------------------------------------
# T8.3 — positive, #1684 shape
# ---------------------------------------------------------------------------


def test_rule_fires_on_direct_fallthrough_1684_shape():
    """A review-pr step's on_failure routing straight to check_repo_ci_event
    (the exact #1684 shape) must fire exactly one finding naming the step
    and the gate."""
    recipe = _make_recipe(
        {
            "review_pr": _review_pr_step(on_failure="check_repo_ci_event"),
            "check_review_posted": RecipeStep(tool="run_python"),
            "check_review_loop": RecipeStep(tool="run_python"),
            "check_repo_ci_event": RecipeStep(tool="check_repo_merge_state"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == RULE_ID]
    assert len(findings) == 1
    assert findings[0].step_name == "review_pr"
    assert "check_repo_ci_event" in findings[0].message


# ---------------------------------------------------------------------------
# T8.4 — positive, multi-hop
# ---------------------------------------------------------------------------


def test_rule_fires_across_intermediate_step():
    """The advance gate need not be the direct on_failure target — an
    intermediate step routing onward to it without crossing the barrier
    also fires."""
    recipe = _make_recipe(
        {
            "review_pr": _review_pr_step(on_failure="intermediate_step"),
            "intermediate_step": RecipeStep(tool="run_python", on_success="check_review_loop"),
            "check_review_posted": RecipeStep(tool="run_python"),
            "check_review_loop": RecipeStep(tool="run_python"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == RULE_ID]
    assert len(findings) == 1
    assert findings[0].step_name == "review_pr"
    assert "check_review_loop" in findings[0].message


# ---------------------------------------------------------------------------
# T8.5 — negative, failure-family routing
# ---------------------------------------------------------------------------


def test_rule_silent_when_failure_edges_terminate_in_failure_family():
    """Failure edges routing to a failure-family terminal chain
    (release_issue_failure -> register_clone_failure) never reach an
    advance gate — zero findings."""
    recipe = _make_recipe(
        {
            "review_pr": _review_pr_step(on_failure="release_issue_failure"),
            "release_issue_failure": RecipeStep(
                tool="release_issue", on_success="register_clone_failure"
            ),
            "register_clone_failure": RecipeStep(tool="remove_clone"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == RULE_ID]
    assert not findings


# ---------------------------------------------------------------------------
# T8.6 — negative, merge-prs shape (barrier absorbs the advance path)
# ---------------------------------------------------------------------------


def test_rule_silent_when_rebase_retry_crosses_barrier_before_advance_gate():
    """merge-prs' rebase-retry loop: resolve-review's on_context_limit
    re-enters through a rebase/push chain whose on_success crosses
    check_review_posted before any advance gate — legitimate, zero
    findings (the barrier absorbs the advance path)."""
    recipe = _make_recipe(
        {
            "resolve_review_integration": _resolve_review_step(
                on_context_limit="rebase_step",
            ),
            "rebase_step": RecipeStep(tool="run_git", on_success="push_step"),
            "push_step": RecipeStep(tool="push_to_remote", on_success="check_review_posted"),
            "check_review_posted": RecipeStep(
                tool="run_python", on_success="derive_batch_ci_event"
            ),
            "derive_batch_ci_event": RecipeStep(tool="run_python"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == RULE_ID]
    assert not findings
