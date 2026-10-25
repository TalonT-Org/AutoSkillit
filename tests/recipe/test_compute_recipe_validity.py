"""Branch coverage for compute_recipe_validity."""

from __future__ import annotations

import pytest

from autoskillit.core import RecipeBindingProjection
from autoskillit.core.types import Severity
from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe.registry import RuleFinding, compute_recipe_validity
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestComputeRecipeValidity:
    def test_all_clean_returns_true(self) -> None:
        assert (
            compute_recipe_validity([], [], [], binding_projection=RecipeBindingProjection({}))
            is True
        )

    def test_schema_error_returns_false(self) -> None:
        assert (
            compute_recipe_validity(
                ["missing name field"], [], [], binding_projection=RecipeBindingProjection({})
            )
            is False
        )

    def test_semantic_error_returns_false(self) -> None:
        finding = RuleFinding(
            rule="test-rule", severity=Severity.ERROR, step_name="s1", message="fail"
        )
        assert (
            compute_recipe_validity(
                [], [finding], [], binding_projection=RecipeBindingProjection({})
            )
            is False
        )

    def test_semantic_warning_returns_true(self) -> None:
        finding = RuleFinding(
            rule="test-rule", severity=Severity.WARNING, step_name="s1", message="warn"
        )
        assert (
            compute_recipe_validity(
                [], [finding], [], binding_projection=RecipeBindingProjection({})
            )
            is True
        )

    def test_contract_error_returns_false(self) -> None:
        finding = {"rule": "contract-unsatisfied-input", "severity": "error", "step": "s1"}
        assert (
            compute_recipe_validity(
                [], [], [finding], binding_projection=RecipeBindingProjection({})
            )
            is False
        )

    def test_contract_warning_returns_true(self) -> None:
        finding = {"rule": "stale-contract", "severity": "warning", "step": "s1"}
        assert (
            compute_recipe_validity(
                [], [], [finding], binding_projection=RecipeBindingProjection({})
            )
            is True
        )

    def test_multiple_error_sources_returns_false(self) -> None:
        semantic = RuleFinding(rule="r1", severity=Severity.ERROR, step_name="s1", message="fail")
        contract = {"rule": "r2", "severity": "error", "step": "s2"}
        assert (
            compute_recipe_validity(
                ["schema err"],
                [semantic],
                [contract],
                binding_projection=RecipeBindingProjection({}),
            )
            is False
        )

    def test_binding_failures_invalidate_recipe(self) -> None:
        invalid = Recipe(
            name="binding-invalid",
            description="A required run_cmd parameter is missing.",
            steps={"step": RecipeStep(name="step", tool="run_cmd", with_args={"cmd": "pwd"})},
        )
        valid = Recipe(
            name="binding-valid",
            description="All required run_cmd parameters are present.",
            steps={
                "step": RecipeStep(
                    name="step", tool="run_cmd", with_args={"cmd": "pwd", "cwd": "/r"}
                )
            },
        )

        assert (
            compute_recipe_validity([], [], [], binding_projection=bind_recipe(invalid)) is False
        )
        assert compute_recipe_validity([], [], [], binding_projection=bind_recipe(valid)) is True
