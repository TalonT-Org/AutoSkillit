"""Tests for sprint_mode ingredient and sprint_entry step in implementation.yaml."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._api import _build_active_recipe, format_ingredients_table
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.validator import validate_recipe


@pytest.fixture(scope="module")
def impl_recipe():
    return load_recipe(builtin_recipes_dir() / "implementation.yaml")


def test_implementation_has_sprint_mode_ingredient(impl_recipe) -> None:
    """implementation.yaml declares 'sprint_mode' ingredient with default 'false'."""
    assert "sprint_mode" in impl_recipe.ingredients
    assert impl_recipe.ingredients["sprint_mode"].default == "false"


def test_implementation_sprint_mode_is_hidden(impl_recipe) -> None:
    """implementation.yaml sprint_mode ingredient has hidden=True."""
    assert impl_recipe.ingredients["sprint_mode"].hidden is True


def test_implementation_has_sprint_entry_step(impl_recipe) -> None:
    """implementation.yaml has 'sprint_entry' step with sub_recipe='sprint-prefix'."""
    assert "sprint_entry" in impl_recipe.steps
    step = impl_recipe.steps["sprint_entry"]
    assert step.sub_recipe == "sprint-prefix"


def test_implementation_sprint_entry_gates_on_sprint_mode(impl_recipe) -> None:
    """implementation.yaml sprint_entry step has gate='sprint_mode'."""
    step = impl_recipe.steps["sprint_entry"]
    assert step.gate == "sprint_mode"


def test_implementation_sprint_entry_on_success_is_done(impl_recipe) -> None:
    """implementation.yaml sprint_entry.on_success == 'done'."""
    step = impl_recipe.steps["sprint_entry"]
    assert step.on_success == "done"


def test_implementation_sprint_mode_false_loads_clean() -> None:
    """load_and_validate('implementation') with sprint_mode=false drops sprint_entry step."""
    active, combined = _build_active_recipe(
        load_recipe(builtin_recipes_dir() / "implementation.yaml"),
        None,
        pkg_root().parent,
    )
    assert "sprint_entry" not in active.steps
    assert combined is None


def test_implementation_sprint_mode_false_first_step_is_clone() -> None:
    """When sprint_mode=false, served recipe's first step is 'clone'."""
    active, _ = _build_active_recipe(
        load_recipe(builtin_recipes_dir() / "implementation.yaml"),
        None,
        pkg_root().parent,
    )
    first_step_name = next(iter(active.steps))
    assert first_step_name == "clone"


def test_implementation_sprint_mode_false_no_sprint_steps_in_content() -> None:
    """When sprint_mode=false, served recipe content contains no sprint_ steps."""
    active, _ = _build_active_recipe(
        load_recipe(builtin_recipes_dir() / "implementation.yaml"),
        None,
        pkg_root().parent,
    )
    sprint_steps = [n for n in active.steps if n.startswith("sprint_")]
    assert not sprint_steps, f"Sprint steps should be absent: {sprint_steps}"


def test_implementation_sprint_mode_true_has_sprint_steps() -> None:
    """load_and_validate('implementation', overrides={sprint_mode: true}) has sprint_ steps."""
    active, combined = _build_active_recipe(
        load_recipe(builtin_recipes_dir() / "implementation.yaml"),
        {"sprint_mode": "true"},
        pkg_root().parent,
    )
    assert combined is not None
    sprint_steps = [n for n in active.steps if "sprint" in n]
    assert sprint_steps, "Sprint steps should appear when sprint_mode=true"


def test_implementation_sprint_mode_true_sprint_triage_is_first() -> None:
    """When sprint_mode=true, sprint_triage is the first step in served recipe."""
    active, _ = _build_active_recipe(
        load_recipe(builtin_recipes_dir() / "implementation.yaml"),
        {"sprint_mode": "true"},
        pkg_root().parent,
    )
    first_step_name = next(iter(active.steps))
    # The first merged step should contain 'triage' (prefixed from sprint-prefix)
    assert "triage" in first_step_name, (
        f"First step should be a sprint triage step, got: {first_step_name}"
    )


def test_implementation_sprint_mode_false_hidden_ingredient_not_in_table() -> None:
    """format_ingredients_table for implementation (sprint_mode=false) excludes sprint_mode."""
    active, _ = _build_active_recipe(
        load_recipe(builtin_recipes_dir() / "implementation.yaml"),
        None,
        pkg_root().parent,
    )
    table = format_ingredients_table(active)
    assert table is None or "sprint_mode" not in (table or "")


def test_implementation_structural_validation_clean(impl_recipe) -> None:
    """validate_recipe(implementation) returns no errors (standalone path)."""
    errors = validate_recipe(impl_recipe)
    assert not errors, f"Structural validation errors: {errors}"


def test_implementation_no_semantic_errors(impl_recipe) -> None:
    """Semantic rules on implementation.yaml (sprint_mode=false) return no ERRORs."""
    from autoskillit.core.types import Severity

    active, _ = _build_active_recipe(impl_recipe, None, pkg_root().parent)
    ctx = make_validation_context(active, available_sub_recipes=frozenset({"sprint-prefix"}))
    errors = [f for f in run_semantic_rules(ctx) if f.severity == Severity.ERROR]
    assert not errors, f"Semantic errors: {errors}"
