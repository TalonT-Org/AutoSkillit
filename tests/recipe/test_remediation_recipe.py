"""Structural tests for remediation.yaml recipe."""

from pathlib import Path

import pytest

from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes" / "remediation.yaml"
)


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RECIPE_PATH)


def test_remediation_recipe_has_release_issue_success_step(recipe):
    """remediation.yaml must have a release_issue step on the success path.

    Absence of this step means issues resolved via remediation never get the
    staged label applied.
    """
    errors = validate_recipe(recipe)
    assert not errors, f"remediation.yaml failed schema validation: {errors}"
    step_names = list(recipe.steps.keys())
    assert any("release_issue" in name and "success" in name for name in step_names), (
        "remediation.yaml is missing a release_issue step on the success path. "
        "Without it, issues are never promoted to staged state after a successful remediation."
    )


# T_REM_LOOP1
def test_check_review_loop_step_exists(recipe) -> None:
    """check_review_loop step must exist in remediation recipe."""
    assert "check_review_loop" in recipe.steps


# T_REM_LOOP2
def test_re_push_review_routes_to_check_review_loop(recipe) -> None:
    """re_push_review on_success must route to check_review_loop in remediation recipe."""
    step = recipe.steps["re_push_review"]
    assert step.on_success == "check_review_loop"


# T_REM_LOOP3
def test_check_review_loop_on_result_routes_to_review_pr_when_had_blocking_and_not_max_exceeded(
    recipe,
) -> None:
    """check_review_loop on_result condition must gate on had_blocking AND max_exceeded."""
    step = recipe.steps["check_review_loop"]
    assert step.on_result is not None
    review_conditions = [
        c for c in step.on_result.conditions if c.when is not None and c.route == "review_pr"
    ]
    assert review_conditions, "No conditional route to review_pr found"
    cond = review_conditions[0].when
    assert "had_blocking" in cond
    assert "max_exceeded" in cond


# T_REM_LOOP4
def test_check_review_loop_has_on_failure(recipe) -> None:
    """check_review_loop must declare on_failure because it uses on_result
    (on-result-missing-failure-route semantic rule requires it)."""
    step = recipe.steps["check_review_loop"]
    assert step.on_failure == "check_repo_ci_event"


# T_REM_LOOP5
def test_review_pr_routes_approved_with_comments_to_resolve_review(recipe) -> None:
    """review_pr on_result must route approved_with_comments to resolve_review."""
    step = recipe.steps["review_pr"]
    assert step.on_result is not None
    routes = {c.when: c.route for c in step.on_result.conditions if c.when}
    matching = [
        when
        for when, route in routes.items()
        if "approved_with_comments" in when and route == "resolve_review"
    ]
    assert matching, "No approved_with_comments → resolve_review route found"


# T_REM_LOOP6
def test_review_pr_captures_review_verdict(recipe) -> None:
    """review_pr must capture verdict as review_verdict (not verdict) to avoid clobber."""
    step = recipe.steps["review_pr"]
    capture = step.capture or {}
    assert "review_verdict" in capture
    assert "result.verdict" in capture["review_verdict"]


# T_REM_LOOP7
def test_check_review_loop_with_args_has_previous_verdict(recipe) -> None:
    """check_review_loop with: must pass previous_verdict from context.review_verdict."""
    step = recipe.steps["check_review_loop"]
    assert "previous_verdict" in step.with_args
    assert "review_verdict" in step.with_args["previous_verdict"]


def test_remediation_next_or_done_step_exists(recipe) -> None:
    """T_REM_MP1: remediation.yaml must have a next_or_done routing step."""
    assert "next_or_done" in recipe.steps
    step = recipe.steps["next_or_done"]
    assert step.action == "route"


def test_remediation_next_or_done_routes_more_parts_to_dry_walkthrough(recipe) -> None:
    """T_REM_MP2: next_or_done must route more_parts back to dry_walkthrough."""
    step = recipe.steps["next_or_done"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    assert any(
        c.route == "dry_walkthrough" and c.when is not None and "more_parts" in c.when
        for c in conds
    ), "next_or_done must have a predicate routing more_parts → dry_walkthrough"


def test_remediation_next_or_done_routes_done_to_push(recipe) -> None:
    """T_REM_MP3: next_or_done fallthrough must route to push (all parts complete)."""
    step = recipe.steps["next_or_done"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    assert any(c.route == "push" for c in conds), (
        "next_or_done must have a fallthrough condition routing to push"
    )


def test_remediation_merge_routes_to_next_or_done(recipe) -> None:
    """T_REM_MP4: merge step default route must be next_or_done, not push."""
    step = recipe.steps["merge"]
    assert step.on_result is not None
    default_routes = [c.route for c in step.on_result.conditions if c.when is None]
    assert default_routes == ["next_or_done"], (
        f"merge default route must be next_or_done, got {default_routes}"
    )


def test_remediation_has_no_sprint_mode_ingredient() -> None:
    """remediation.yaml must not declare sprint_mode after sprint-prefix removal."""
    recipe = load_recipe(RECIPE_PATH)
    assert "sprint_mode" not in recipe.ingredients


def test_remediation_has_no_sprint_entry_step() -> None:
    """remediation.yaml must not have a sprint_entry step after sprint-prefix removal."""
    recipe = load_recipe(RECIPE_PATH)
    assert "sprint_entry" not in recipe.steps


def test_remediation_validates_clean_after_sprint_removal() -> None:
    """remediation.yaml must pass schema validation after sprint references removed."""
    recipe = load_recipe(RECIPE_PATH)
    errors = validate_recipe(recipe)
    assert not errors, f"remediation.yaml failed validation after sprint removal: {errors}"
