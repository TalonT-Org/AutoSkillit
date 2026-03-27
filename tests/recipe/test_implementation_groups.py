"""Structural assertions for the bundled implementation-groups recipe."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")


def test_ig_re_push_has_force_true(recipe) -> None:
    """re_push step in implementation-groups must have force='true'."""
    assert "re_push" in recipe.steps
    step = recipe.steps["re_push"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "re_push must include force='true' — post-rebase force push required"
    )


def test_ig_re_push_queue_fix_has_force_true(recipe) -> None:
    """re_push_queue_fix step in implementation-groups must have force='true'."""
    assert "re_push_queue_fix" in recipe.steps
    step = recipe.steps["re_push_queue_fix"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "re_push_queue_fix must include force='true' — post-rebase force push required"
    )


def test_ig_re_push_direct_fix_has_force_true(recipe) -> None:
    """re_push_direct_fix step in implementation-groups must have force='true'."""
    assert "re_push_direct_fix" in recipe.steps
    step = recipe.steps["re_push_direct_fix"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "re_push_direct_fix must include force='true' — post-rebase force push required"
    )


def test_ig_re_push_immediate_fix_has_force_true(recipe) -> None:
    """re_push_immediate_fix step in implementation-groups must have force='true'."""
    assert "re_push_immediate_fix" in recipe.steps
    step = recipe.steps["re_push_immediate_fix"]
    assert step.tool == "push_to_remote"
    assert step.with_args.get("force") == "true", (
        "re_push_immediate_fix must include force='true' — post-rebase force push required"
    )
