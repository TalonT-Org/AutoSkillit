"""Tests for recipe_schema module — pure domain model."""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


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
# RECIPE-8: constant step type
# ---------------------------------------------------------------------------


def test_recipe_step_has_constant_field() -> None:
    """RecipeStep has a constant field defaulting to None."""
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep()
    assert step.constant is None


def test_constant_step_parse_from_yaml() -> None:
    """A constant step is parsed from YAML into RecipeStep.constant."""
    from autoskillit.recipe.io import _parse_step

    step = _parse_step({"constant": "main"})
    assert step.constant == "main"
    assert step.tool is None
    assert step.action is None
    assert step.python is None


def test_recipe_step_fields_includes_constant() -> None:
    """RecipeStep field set includes 'constant'."""
    import dataclasses

    from autoskillit.recipe.schema import RecipeStep

    field_names = {f.name for f in dataclasses.fields(RecipeStep)}
    assert "constant" in field_names


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


def test_terminal_targets_defined_in_schema():
    """Schema is the authoritative home for routing sentinel constants."""
    from autoskillit.recipe.schema import _TERMINAL_TARGETS

    assert "escalate" in _TERMINAL_TARGETS
    assert "done" in _TERMINAL_TARGETS


def test_recipe_dataclass_has_requires_packs_field():
    from autoskillit.recipe.schema import Recipe

    fields = {f.name for f in dataclasses.fields(Recipe)}
    assert "requires_packs" in fields
    r = Recipe(name="x", description="y")
    assert r.requires_packs == []


def test_recipe_has_content_hash_field():
    from autoskillit.recipe.schema import Recipe

    r = Recipe(name="test", description="d")
    assert r.content_hash == ""


def test_recipe_has_composite_hash_field():
    from autoskillit.recipe.schema import Recipe

    r = Recipe(name="test", description="d")
    assert r.composite_hash == ""


def test_recipe_has_recipe_version_field():
    from autoskillit.recipe.schema import Recipe

    r = Recipe(name="test", description="d")
    assert r.recipe_version is None


def test_recipe_info_has_content_hash_field():
    from autoskillit.core.types import RecipeSource
    from autoskillit.recipe.schema import RecipeInfo

    ri = RecipeInfo(
        name="t", description="d", source=RecipeSource.BUILTIN, path=pathlib.Path("/x")
    )
    assert ri.content_hash == ""


def test_recipe_info_has_recipe_version_field():
    from autoskillit.core.types import RecipeSource
    from autoskillit.recipe.schema import RecipeInfo

    ri = RecipeInfo(
        name="t", description="d", source=RecipeSource.BUILTIN, path=pathlib.Path("/x")
    )
    assert ri.recipe_version is None


# ---------------------------------------------------------------------------
# RecipeKind, CampaignDispatch, Recipe new fields (franchise schema extension)
# ---------------------------------------------------------------------------


def test_recipe_kind_enum_defined() -> None:
    from enum import StrEnum

    from autoskillit.recipe.schema import RecipeKind

    assert issubclass(RecipeKind, StrEnum)
    assert RecipeKind.STANDARD == "standard"
    assert RecipeKind.CAMPAIGN == "campaign"
    assert len(RecipeKind) == 2


def test_campaign_dispatch_dataclass() -> None:
    from autoskillit.recipe.schema import CampaignDispatch

    assert dataclasses.is_dataclass(CampaignDispatch)
    d = CampaignDispatch(name="impl", recipe="implementation", task="build feature")
    assert d.name == "impl"
    assert d.recipe == "implementation"
    assert d.task == "build feature"
    assert d.ingredients == {}
    assert d.depends_on == []


def test_recipe_has_kind_field_defaulting_to_standard() -> None:
    from autoskillit.recipe.schema import Recipe, RecipeKind

    r = Recipe(name="x", description="y")
    assert r.kind == RecipeKind.STANDARD


def test_recipe_has_categories_field_defaulting_to_empty() -> None:
    from autoskillit.recipe.schema import Recipe

    r = Recipe(name="x", description="y")
    assert r.categories == []


def test_recipe_has_dispatches_field_defaulting_to_empty() -> None:
    from autoskillit.recipe.schema import Recipe

    r = Recipe(name="x", description="y")
    assert r.dispatches == []


def test_recipe_has_requires_recipe_packs_field_defaulting_to_empty() -> None:
    from autoskillit.recipe.schema import Recipe

    r = Recipe(name="x", description="y")
    assert r.requires_recipe_packs == []


def test_recipe_has_allowed_recipes_field_defaulting_to_empty() -> None:
    from autoskillit.recipe.schema import Recipe

    r = Recipe(name="x", description="y")
    assert r.allowed_recipes == []


def test_recipe_has_continue_on_failure_field_defaulting_to_false() -> None:
    from autoskillit.recipe.schema import Recipe

    r = Recipe(name="x", description="y")
    assert r.continue_on_failure is False


def test_existing_recipe_construction_unchanged() -> None:
    """All pre-existing Recipe construction patterns still work."""
    from autoskillit.recipe.schema import Recipe, RecipeKind, RecipeStep

    r = Recipe(
        name="test",
        description="test recipe",
        steps={"stop": RecipeStep(action="stop")},
        requires_packs=["github"],
    )
    assert r.kind == RecipeKind.STANDARD
    assert r.categories == []
    assert r.dispatches == []
    assert r.requires_recipe_packs == []
    assert r.allowed_recipes == []
    assert r.continue_on_failure is False


def test_campaign_recipe_construction() -> None:
    from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeKind

    r = Recipe(
        name="multi-impl",
        description="campaign",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[
            CampaignDispatch(
                name="phase-1",
                recipe="implementation",
                task="Build feature A",
                ingredients={"branch": "feature-a"},
                depends_on=[],
            ),
            CampaignDispatch(
                name="phase-2",
                recipe="implementation",
                task="Build feature B",
                depends_on=["phase-1"],
            ),
        ],
        requires_recipe_packs=["implementation-family"],
        continue_on_failure=True,
    )
    assert r.kind == RecipeKind.CAMPAIGN
    assert len(r.dispatches) == 2
    assert r.dispatches[1].depends_on == ["phase-1"]
    assert r.requires_recipe_packs == ["implementation-family"]
    assert r.continue_on_failure is True
    assert r.steps == {}
    assert r.requires_packs == []
    assert r.allowed_recipes == []
    assert r.categories == []
