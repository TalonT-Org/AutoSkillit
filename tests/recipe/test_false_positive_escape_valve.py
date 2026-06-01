"""Tests for false-positive escape valve routing in recipes that invoke make-plan."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.fixture(scope="module")
def impl_recipe():
    return load_recipe(builtin_recipes_dir() / "implementation.yaml")


@pytest.fixture(scope="module")
def groups_recipe():
    return load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")


@pytest.fixture(scope="module")
def remed_recipe():
    return load_recipe(builtin_recipes_dir() / "remediation.yaml")


def test_impl_plan_step_uses_on_result(impl_recipe):
    """plan step must use on_result, not on_success."""
    step = impl_recipe.steps["plan"]
    assert step.on_result is not None
    assert step.on_success is None


def test_impl_plan_step_captures_verdict(impl_recipe):
    """plan step must capture result.verdict."""
    step = impl_recipe.steps["plan"]
    assert "verdict" in step.capture
    assert "result.verdict" in step.capture["verdict"].from_


def test_impl_plan_routes_false_positive_to_check_has_commits(impl_recipe):
    """verdict=false_positive must route to check_has_commits."""
    step = impl_recipe.steps["plan"]
    fp_routes = [c for c in step.on_result.conditions if c.when and "false_positive" in c.when]
    assert len(fp_routes) == 1
    assert fp_routes[0].route == "check_has_commits"


def test_impl_plan_routes_plan_to_review_approach(impl_recipe):
    """verdict=plan must route to review_approach."""
    step = impl_recipe.steps["plan"]
    plan_routes = [
        c
        for c in step.on_result.conditions
        if c.when and "== plan" in c.when and "false_positive" not in c.when
    ]
    assert len(plan_routes) == 1
    assert plan_routes[0].route == "review_approach"


def test_groups_plan_step_uses_on_result(groups_recipe):
    """plan step must use on_result, not on_success."""
    step = groups_recipe.steps["plan"]
    assert step.on_result is not None
    assert step.on_success is None


def test_groups_plan_step_captures_verdict(groups_recipe):
    """plan step must capture result.verdict."""
    step = groups_recipe.steps["plan"]
    assert "verdict" in step.capture
    assert "result.verdict" in step.capture["verdict"].from_


def test_groups_plan_routes_false_positive_to_push(groups_recipe):
    """verdict=false_positive must route to push in groups recipe."""
    step = groups_recipe.steps["plan"]
    fp_routes = [c for c in step.on_result.conditions if c.when and "false_positive" in c.when]
    assert len(fp_routes) == 1
    assert fp_routes[0].route == "push"


def test_remed_make_plan_step_uses_on_result(remed_recipe):
    """make_plan step must use on_result, not on_success."""
    step = remed_recipe.steps["make_plan"]
    assert step.on_result is not None
    assert step.on_success is None


def test_remed_make_plan_step_captures_verdict(remed_recipe):
    """make_plan step must capture result.verdict."""
    step = remed_recipe.steps["make_plan"]
    assert "verdict" in step.capture
    assert "result.verdict" in step.capture["verdict"].from_


def test_remed_make_plan_routes_false_positive_to_check_has_commits(remed_recipe):
    """verdict=false_positive must route to check_has_commits."""
    step = remed_recipe.steps["make_plan"]
    fp_routes = [c for c in step.on_result.conditions if c.when and "false_positive" in c.when]
    assert len(fp_routes) == 1
    assert fp_routes[0].route == "check_has_commits"


def test_remed_make_plan_routes_plan_to_dry_walkthrough(remed_recipe):
    """verdict=plan must route to dry_walkthrough."""
    step = remed_recipe.steps["make_plan"]
    plan_routes = [
        c
        for c in step.on_result.conditions
        if c.when and "== plan" in c.when and "false_positive" not in c.when
    ]
    assert len(plan_routes) == 1
    assert plan_routes[0].route == "dry_walkthrough"
