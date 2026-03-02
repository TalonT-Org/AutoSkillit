"""Tests for recipe_schema module — pure domain model."""

from __future__ import annotations

import ast
import dataclasses
import pathlib


def test_all_dataclasses_importable() -> None:
    """All dataclasses are importable from recipe.schema."""
    from autoskillit.recipe.schema import (
        DataFlowReport,
        DataFlowWarning,
        Recipe,
        RecipeInfo,
        RecipeIngredient,
        RecipeStep,
        StepResultCondition,
        StepResultRoute,
        StepRetry,
    )

    assert Recipe is not None
    assert RecipeStep is not None
    assert RecipeIngredient is not None
    assert RecipeInfo is not None
    assert DataFlowWarning is not None
    assert DataFlowReport is not None
    assert StepRetry is not None
    assert StepResultRoute is not None
    assert StepResultCondition is not None


def test_recipe_schema_has_zero_non_stdlib_logic_imports() -> None:
    """recipe/schema.py has zero non-stdlib logic imports (no _logging, no yaml)."""
    src = (
        pathlib.Path(__file__).parent.parent.parent / "src/autoskillit/recipe/schema.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "autoskillit" not in node.module or node.module in (
                "autoskillit.core.types",
                "autoskillit.core",
            ), f"recipe/schema.py imports non-types autoskillit module: {node.module}"


def test_autoskillit_version_key_constant_exists() -> None:
    """AUTOSKILLIT_VERSION_KEY constant is exported from recipe.schema."""
    from autoskillit.recipe.schema import AUTOSKILLIT_VERSION_KEY

    assert AUTOSKILLIT_VERSION_KEY == "autoskillit_version"


def test_recipe_step_has_expected_fields() -> None:
    """RecipeStep has expected fields: tool, action, python, model, note, capture, etc."""
    from autoskillit.recipe.schema import RecipeStep

    fields = {f.name for f in dataclasses.fields(RecipeStep)}
    assert {
        "tool",
        "action",
        "python",
        "model",
        "note",
        "capture",
        "on_success",
        "on_failure",
    } <= fields


def test_recipe_step_has_on_retry_field() -> None:
    """RecipeStep must support on_retry as a first-class routing field."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep(
        tool="run_skill",
        on_success="done",
        on_failure="cleanup",
        on_retry="verify",
        with_args={"skill_command": "test", "cwd": "/tmp"},
    )
    assert step.on_retry == "verify"


def test_step_result_condition_dataclass_exists() -> None:
    """StepResultCondition is importable and has route and when fields."""
    from autoskillit.recipe.schema import StepResultCondition

    cond = StepResultCondition(route="assess")
    assert cond.route == "assess"
    assert cond.when is None

    cond2 = StepResultCondition(when="result.failed_step == 'test_gate'", route="assess")
    assert cond2.when == "result.failed_step == 'test_gate'"
    assert cond2.route == "assess"


def test_step_result_route_has_conditions_field() -> None:
    """StepResultRoute has a conditions field that defaults to an empty list."""
    from autoskillit.recipe.schema import StepResultRoute

    route = StepResultRoute()
    assert route.conditions == []


def test_step_result_route_is_predicate_when_conditions_non_empty() -> None:
    """StepResultRoute with non-empty conditions is predicate format (conditions != [])."""
    from autoskillit.recipe.schema import StepResultCondition, StepResultRoute

    route = StepResultRoute(
        conditions=[
            StepResultCondition(when="result.error", route="cleanup"),
            StepResultCondition(route="push"),
        ]
    )
    assert len(route.conditions) == 2


def test_skip_when_false_field_exists_on_recipe_step() -> None:
    """RecipeStep must have a skip_when_false field defaulting to None."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep(tool="run_skill")
    assert hasattr(step, "skip_when_false")
    assert step.skip_when_false is None


def test_skip_when_false_field_is_parsed_from_yaml() -> None:
    """skip_when_false must be deserialized from YAML recipe data."""
    from autoskillit.recipe.io import _parse_step

    raw = {
        "tool": "run_skill",
        "skip_when_false": "inputs.open_pr",
        "on_success": "next_step",
    }
    step = _parse_step(raw)
    assert step.skip_when_false == "inputs.open_pr"
