"""Tests for the sprint-prefix sub-recipe content and structure."""

from __future__ import annotations

import pytest

from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.io import builtin_sub_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe")]


@pytest.fixture(scope="module")
def sprint_prefix_recipe():
    path = builtin_sub_recipes_dir() / "sprint-prefix.yaml"
    return load_recipe(path)


def test_sprint_sub_recipe_loads(sprint_prefix_recipe) -> None:
    """sprint-prefix.yaml parses without errors."""
    assert sprint_prefix_recipe.name == "sprint-prefix"


def test_sprint_sub_recipe_structural_validation_clean(sprint_prefix_recipe) -> None:
    """validate_recipe(sprint_prefix) returns no errors."""
    errors = validate_recipe(sprint_prefix_recipe)
    assert not errors, f"Structural validation errors: {errors}"


def test_sprint_sub_recipe_no_semantic_errors(sprint_prefix_recipe) -> None:
    """run_semantic_rules on sprint-prefix has no ERROR-severity findings."""
    from autoskillit.core.types import Severity

    ctx = make_validation_context(
        sprint_prefix_recipe,
        available_skills=frozenset(
            {
                "triage-issues",
                "sprint-planner",
                "process-issues",
            }
        ),
    )
    errors = [f for f in run_semantic_rules(ctx) if f.severity == Severity.ERROR]
    assert not errors, f"Semantic errors: {errors}"


def test_sprint_sub_recipe_has_triage_step(sprint_prefix_recipe) -> None:
    """sprint-prefix.yaml has a 'triage' step calling triage-issues skill."""
    assert "triage" in sprint_prefix_recipe.steps
    triage = sprint_prefix_recipe.steps["triage"]
    assert triage.tool == "run_skill"
    skill_cmd = triage.with_args.get("skill_command", "")
    assert "triage-issues" in skill_cmd


def test_sprint_sub_recipe_has_plan_step(sprint_prefix_recipe) -> None:
    """sprint-prefix.yaml has a 'plan' step for sprint planning."""
    assert "plan" in sprint_prefix_recipe.steps
    plan = sprint_prefix_recipe.steps["plan"]
    assert plan.tool == "run_skill"
    skill_cmd = plan.with_args.get("skill_command", "")
    assert "sprint-planner" in skill_cmd


def test_sprint_sub_recipe_has_confirm_step(sprint_prefix_recipe) -> None:
    """sprint-prefix.yaml has a confirm action step for user approval."""
    assert "confirm" in sprint_prefix_recipe.steps
    confirm = sprint_prefix_recipe.steps["confirm"]
    assert confirm.action == "confirm"


def test_sprint_sub_recipe_has_dispatch_step(sprint_prefix_recipe) -> None:
    """sprint-prefix.yaml has a dispatch step calling process-issues."""
    assert "dispatch" in sprint_prefix_recipe.steps
    dispatch = sprint_prefix_recipe.steps["dispatch"]
    assert dispatch.tool == "run_skill"
    skill_cmd = dispatch.with_args.get("skill_command", "")
    assert "process-issues" in skill_cmd


def test_sprint_sub_recipe_has_report_step(sprint_prefix_recipe) -> None:
    """sprint-prefix.yaml has a done-terminal report step."""
    assert "report" in sprint_prefix_recipe.steps
    report = sprint_prefix_recipe.steps["report"]
    assert report.action == "stop"


def test_sprint_sub_recipe_has_sprint_size_ingredient(sprint_prefix_recipe) -> None:
    """sprint-prefix.yaml declares 'sprint_size' ingredient with default '4'."""
    assert "sprint_size" in sprint_prefix_recipe.ingredients
    ingredient = sprint_prefix_recipe.ingredients["sprint_size"]
    assert ingredient.default == "4"


def test_sprint_sub_recipe_terminal_routes_to_done(sprint_prefix_recipe) -> None:
    """All terminal paths in sprint-prefix.yaml end at 'done' or 'escalate'."""
    terminal_names = {"done", "escalate", "report"}
    for name, step in sprint_prefix_recipe.steps.items():
        if name in terminal_names:
            assert step.action == "stop", f"Step '{name}' should be action: stop"


def test_sprint_sub_recipe_kitchen_rules_present(sprint_prefix_recipe) -> None:
    """sprint-prefix.yaml has non-empty kitchen_rules."""
    assert sprint_prefix_recipe.kitchen_rules, "sprint-prefix.yaml must have kitchen_rules"
    assert len(sprint_prefix_recipe.kitchen_rules) >= 1


def test_sprint_sub_recipe_diagram_exists() -> None:
    """Diagram file exists at recipes/sub-recipes/diagrams/sprint-prefix.md."""
    diagram_path = builtin_sub_recipes_dir() / "diagrams" / "sprint-prefix.md"
    assert diagram_path.exists(), f"Diagram not found: {diagram_path}"
    content = diagram_path.read_text()
    assert "sprint-prefix" in content
