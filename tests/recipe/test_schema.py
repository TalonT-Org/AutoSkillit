"""Tests for recipe_schema module — pure domain model."""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest


def test_all_dataclasses_importable() -> None:
    """All dataclasses are importable from recipe.schema."""
    from autoskillit.recipe.schema import (
        Recipe,
        RecipeStep,
    )

    assert dataclasses.is_dataclass(Recipe)
    assert dataclasses.is_dataclass(RecipeStep)


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


def test_autoskillit_version_key_parsed_by_recipe(tmp_path) -> None:
    """AUTOSKILLIT_VERSION_KEY is read by load_recipe and stored in Recipe.version."""
    import yaml

    from autoskillit.recipe.io import load_recipe
    from autoskillit.recipe.schema import AUTOSKILLIT_VERSION_KEY

    data = {
        "name": "v-test",
        "description": "d",
        "steps": {"done": {"action": "stop", "message": "Done."}},
        AUTOSKILLIT_VERSION_KEY: "1.0.0",
    }
    p = tmp_path / "r.yaml"
    p.write_text(yaml.dump(data))
    recipe = load_recipe(p)
    assert recipe.version == "1.0.0"


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


def test_recipe_step_has_retries_field() -> None:
    """RecipeStep must have a retries field defaulting to 3."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep()
    assert step.retries == 3


def test_recipe_step_retries_zero() -> None:
    """RecipeStep supports retries=0 to disable retry."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep(retries=0)
    assert step.retries == 0


def test_recipe_step_has_on_exhausted_field() -> None:
    """RecipeStep must have an on_exhausted field defaulting to 'escalate'."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep()
    assert step.on_exhausted == "escalate"


def test_recipe_step_has_on_context_limit_field() -> None:
    """RecipeStep must have an on_context_limit field defaulting to None."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep()
    assert step.on_context_limit is None


def test_recipe_step_has_no_retry_block() -> None:
    """RecipeStep must not have a 'retry' field (replaced by flat fields)."""
    import dataclasses

    from autoskillit.recipe.schema import RecipeStep

    field_names = {f.name for f in dataclasses.fields(RecipeStep)}
    assert "retry" not in field_names


def test_recipe_step_has_no_on_retry_field() -> None:
    """RecipeStep must not have an 'on_retry' field (replaced by on_context_limit)."""
    import dataclasses

    from autoskillit.recipe.schema import RecipeStep

    field_names = {f.name for f in dataclasses.fields(RecipeStep)}
    assert "on_retry" not in field_names


def test_step_retry_dataclass_removed() -> None:
    """StepRetry dataclass must not exist in recipe.schema."""
    import autoskillit.recipe.schema as schema

    assert not hasattr(schema, "StepRetry")


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

    route = StepResultRoute(field="verdict", routes={"GO": "done"})
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
    assert route.field == ""
    assert route.routes == {}


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


# ---------------------------------------------------------------------------
# RecipeIngredient normalization tests (Tests 1.1-1.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("some description text\n", "some description text"),
        ("First line\nsecond line\n", "First line second line"),
    ],
)
def test_recipe_ingredient_description_strips_folded_scalar_newline(
    raw: str, expected: str
) -> None:
    """RecipeIngredient.__post_init__ must normalize folded-scalar descriptions."""
    from autoskillit.recipe.schema import RecipeIngredient

    ing = RecipeIngredient(description=raw, required=False)
    assert ing.description == expected
    assert "\n" not in ing.description


def test_recipe_ingredient_default_strips_for_comparison() -> None:
    """Trailing \\n on default must not cause _format_ingredient_default to fall through."""
    from autoskillit.recipe.schema import RecipeIngredient

    ing_false = RecipeIngredient(description="d", default="false\n")
    assert ing_false.default == "false"

    ing_empty = RecipeIngredient(description="d", default="\n")
    assert ing_empty.default == ""

    ing_true = RecipeIngredient(description="d", default="true\n")
    assert ing_true.default == "true"

    ing_none = RecipeIngredient(description="d", default=None)
    assert ing_none.default is None  # None sentinel preserved


def test_format_ingredient_default_folded_scalar_bool() -> None:
    """_format_ingredient_default must return 'off'/'on'/'auto-detect' for folded defaults."""
    from autoskillit.recipe.diagrams import _format_ingredient_default
    from autoskillit.recipe.schema import RecipeIngredient

    assert (
        _format_ingredient_default(RecipeIngredient(description="d", default="false\n")) == "off"
    )
    assert _format_ingredient_default(RecipeIngredient(description="d", default="true\n")) == "on"
    assert (
        _format_ingredient_default(RecipeIngredient(description="d", default="\n"))
        == "auto-detect"
    )


# ---------------------------------------------------------------------------
# P9-F1: RecipeStep.description field
# ---------------------------------------------------------------------------


def test_recipe_step_has_description_field() -> None:
    """RecipeStep has a description field defaulting to empty string."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep(tool="run_cmd")
    assert step.description == ""


def test_recipe_step_description_stores_value() -> None:
    """RecipeStep stores an explicit description value."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep(tool="run_cmd", description="Build the project")
    assert step.description == "Build the project"
