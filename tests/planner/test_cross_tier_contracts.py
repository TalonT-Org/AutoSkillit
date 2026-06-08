"""Cross-tier dispatch mode contract tests for the planner recipe."""

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [
    pytest.mark.layer("planner"),
    pytest.mark.small,
    pytest.mark.feature("planner"),
]


@pytest.fixture(scope="module")
def planner_recipe():
    return load_recipe(builtin_recipes_dir() / "planner.yaml")


TIER_DISPATCH_STEPS = {
    "elaborate_phases": "PARALLEL",
    "elaborate_assignments": "PARALLEL",
    "elaborate_wps": "PARALLEL",
    "refine_assignments": "PARALLEL",
    "refine_wps": "PARALLEL",
}


def test_all_tier_dispatch_steps_use_expected_mode(planner_recipe):
    for step_name, expected_mode in TIER_DISPATCH_STEPS.items():
        step = planner_recipe.steps[step_name]
        assert step.note is not None, (
            f"Step '{step_name}' must have a note documenting dispatch mode"
        )
        assert expected_mode.lower() in step.note.lower(), (
            f"Step '{step_name}' expected {expected_mode} dispatch in note"
        )
