from __future__ import annotations

import pytest

from autoskillit.recipe.io import _parse_step
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultRoute
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestMultipartIterationRule:
    def test_mi1_multipart_rule_warns_on_missing_glob_note(self) -> None:
        """T_MI1: multipart-glob-note fires when make-plan step has no *_part_*.md in note."""
        recipe = Recipe(
            name="test-recipe",
            description="test",
            ingredients={},
            steps={
                "plan": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
                    on_success="verify",
                    note="Produces a plan file.",
                ),
                "verify": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:dry-walkthrough context.plan_path"},
                    on_success="done",
                ),
                "done": RecipeStep(action="stop", message="Done"),
            },
            kitchen_rules=[],
        )
        warnings = run_semantic_rules(recipe)
        rule_names = [w.rule for w in warnings]
        assert "multipart-glob-note" in rule_names

    def test_mi2_multipart_rule_passes_compliant_recipe(self) -> None:
        """T_MI2: Validator emits no multipart warnings when all conventions are present."""
        recipe = Recipe(
            name="test-recipe",
            description="test",
            ingredients={},
            steps={
                "plan": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
                    on_success="verify",
                    note="Glob plan_dir for *_part_*.md or single plan file.",
                ),
                "verify": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:dry-walkthrough context.plan_path"},
                    on_success="next_or_done",
                ),
                "next_or_done": RecipeStep(
                    action="route",
                    on_result=StepResultRoute(
                        field="next", routes={"more_parts": "verify", "all_done": "done"}
                    ),
                ),
                "done": RecipeStep(action="stop", message="Done"),
            },
            kitchen_rules=["SEQUENTIAL EXECUTION: complete full cycle per part before advancing."],
        )
        warnings = run_semantic_rules(recipe)
        rule_names = [w.rule for w in warnings]
        assert "multipart-glob-note" not in rule_names
        assert "multipart-sequential-kitchen-rule" not in rule_names
        assert "multipart-route-back" not in rule_names


@pytest.fixture
def compliant_multipart_recipe_no_list() -> Recipe:
    """Recipe with make-plan step but no capture_list for plan_parts."""
    return Recipe(
        name="test",
        description="test",
        ingredients={},
        steps={
            "plan": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
                capture={"plan_path": "${{ result.plan_path }}"},
                note="Glob plan_dir for *_part_*.md or single plan file. Sort into plan_parts[].",
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
        kitchen_rules=["SEQUENTIAL EXECUTION: complete full cycle per part."],
    )


@pytest.fixture
def compliant_multipart_recipe_with_list() -> Recipe:
    """Recipe with make-plan step and correct capture_list for plan_parts."""
    return Recipe(
        name="test",
        description="test",
        ingredients={},
        steps={
            "plan": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
                capture={"plan_path": "${{ result.plan_path }}"},
                capture_list={"plan_parts": "${{ result.plan_parts }}"},
                note="Glob plan_dir for *_part_*.md or single plan file. Sort into plan_parts[].",
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
        kitchen_rules=["SEQUENTIAL EXECUTION: complete full cycle per part."],
    )


def test_validator_warns_when_plan_parts_not_captured(
    compliant_multipart_recipe_no_list: Recipe,
) -> None:
    """D6: Validator warns when make-plan step lacks capture_list for plan_parts."""
    warnings = run_semantic_rules(compliant_multipart_recipe_no_list)
    rule_names = [w.rule for w in warnings]
    assert "multipart-plan-parts-not-captured" in rule_names


def test_validator_passes_when_plan_parts_captured(
    compliant_multipart_recipe_with_list: Recipe,
) -> None:
    """D7: Validator passes when make-plan step has capture_list for plan_parts."""
    warnings = run_semantic_rules(compliant_multipart_recipe_with_list)
    rule_names = [w.rule for w in warnings]
    assert "multipart-plan-parts-not-captured" not in rule_names
