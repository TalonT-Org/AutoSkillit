"""Branch coverage for compute_recipe_validity."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.registry import RuleFinding, compute_recipe_validity


class TestComputeRecipeValidity:
    def test_all_clean_returns_true(self) -> None:
        assert compute_recipe_validity([], [], []) is True

    def test_schema_error_returns_false(self) -> None:
        assert compute_recipe_validity(["missing name field"], [], []) is False

    def test_semantic_error_returns_false(self) -> None:
        finding = RuleFinding(
            rule="test-rule", severity=Severity.ERROR, step_name="s1", message="fail"
        )
        assert compute_recipe_validity([], [finding], []) is False

    def test_semantic_warning_returns_true(self) -> None:
        finding = RuleFinding(
            rule="test-rule", severity=Severity.WARNING, step_name="s1", message="warn"
        )
        assert compute_recipe_validity([], [finding], []) is True

    def test_contract_error_returns_false(self) -> None:
        finding = {"rule": "contract-unsatisfied-input", "severity": "error", "step": "s1"}
        assert compute_recipe_validity([], [], [finding]) is False

    def test_contract_warning_returns_true(self) -> None:
        finding = {"rule": "stale-contract", "severity": "warning", "step": "s1"}
        assert compute_recipe_validity([], [], [finding]) is True

    def test_multiple_error_sources_returns_false(self) -> None:
        semantic = RuleFinding(rule="r1", severity=Severity.ERROR, step_name="s1", message="fail")
        contract = {"rule": "r2", "severity": "error", "step": "s2"}
        assert compute_recipe_validity(["schema err"], [semantic], [contract]) is False
