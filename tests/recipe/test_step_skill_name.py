"""Tests for RecipeStep.skill_name property."""

from __future__ import annotations

import pytest

from autoskillit.recipe.schema import RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_skill_name_bare_form():
    """skill_name normalizes /name → canonical skill name."""
    step = RecipeStep(tool="run_skill", with_args={"skill_command": "/review-pr main feat"})
    assert step.skill_name == "review-pr"


def test_skill_name_namespaced_form():
    """skill_name normalizes /autoskillit:name → canonical skill name."""
    step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:review-pr ${{ context.x }}"},
    )
    assert step.skill_name == "review-pr"


def test_skill_name_non_skill_step():
    """skill_name returns None when with_args has no skill_command."""
    step = RecipeStep(tool="run_python", with_args={})
    assert step.skill_name is None


def test_skill_name_empty_command():
    """skill_name returns None for empty skill_command."""
    step = RecipeStep(tool="run_skill", with_args={"skill_command": ""})
    assert step.skill_name is None


def test_skill_name_different_skill():
    """skill_name correctly extracts name for non-review-pr skills."""
    step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:audit-impl ${{ context.plan_path }}"},
    )
    assert step.skill_name == "audit-impl"
