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


def test_recipe_schema_has_zero_non_stdlib_logic_imports() -> None:
    """recipe/schema.py has zero non-stdlib logic imports (no _logging, no yaml)."""
    src = (pathlib.Path(__file__).parent.parent / "src/autoskillit/recipe/schema.py").read_text()
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
