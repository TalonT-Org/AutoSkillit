"""Tests for sprint_mode ingredient and sprint_entry step in remediation.yaml."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._api import _build_active_recipe
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.validator import validate_recipe

# Known violations fixed in Parts B and C — excluded from general semantic-error assertions.
_NO_AUTOSKILLIT_IMPORT = "no-autoskillit-import-in-skill-python-block"


@pytest.fixture(scope="module")
def rem_recipe():
    return load_recipe(builtin_recipes_dir() / "remediation.yaml")


def test_remediation_has_sprint_mode_ingredient(rem_recipe) -> None:
    """remediation.yaml declares 'sprint_mode' ingredient with default 'false'."""
    assert "sprint_mode" in rem_recipe.ingredients
    assert rem_recipe.ingredients["sprint_mode"].default == "false"


def test_remediation_sprint_mode_is_hidden(rem_recipe) -> None:
    """remediation.yaml sprint_mode ingredient has hidden=True."""
    assert rem_recipe.ingredients["sprint_mode"].hidden is True


def test_remediation_has_sprint_entry_step(rem_recipe) -> None:
    """remediation.yaml has 'sprint_entry' step with sub_recipe='sprint-prefix'."""
    assert "sprint_entry" in rem_recipe.steps
    step = rem_recipe.steps["sprint_entry"]
    assert step.sub_recipe == "sprint-prefix"


def test_remediation_sprint_mode_false_loads_clean() -> None:
    """load_and_validate('remediation') with sprint_mode=false drops sprint_entry step."""
    active, combined = _build_active_recipe(
        load_recipe(builtin_recipes_dir() / "remediation.yaml"),
        None,
        pkg_root().parent,
    )
    assert "sprint_entry" not in active.steps
    assert combined is None


def test_remediation_structural_validation_clean(rem_recipe) -> None:
    """validate_recipe(remediation) returns no errors (standalone path)."""
    errors = validate_recipe(rem_recipe)
    assert not errors, f"Structural validation errors: {errors}"


def test_remediation_no_semantic_errors(rem_recipe) -> None:
    """Semantic rules on remediation.yaml (sprint_mode=false) return no ERRORs."""
    from autoskillit.core.types import Severity

    active, _ = _build_active_recipe(rem_recipe, None, pkg_root().parent)
    ctx = make_validation_context(active, available_sub_recipes=frozenset({"sprint-prefix"}))
    errors = [
        f
        for f in run_semantic_rules(ctx)
        if f.severity == Severity.ERROR and f.rule != _NO_AUTOSKILLIT_IMPORT
    ]
    assert not errors, f"Semantic errors: {errors}"
