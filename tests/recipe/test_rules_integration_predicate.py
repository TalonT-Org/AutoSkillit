from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import NO_AUTOSKILLIT_IMPORT as _NO_AUTOSKILLIT_IMPORT

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestRecipeIntegrationPredicateRouting:
    """Integration tests: bundled recipes with predicate on_result validate correctly."""

    @pytest.fixture(scope="class", autouse=True)
    def _load_recipes(self, request) -> None:
        request.cls.if_recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")
        request.cls.ip_recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")

    def test_investigate_first_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in remediation.yaml has predicate on_result."""
        step = self.if_recipe.steps["merge"]
        assert step.on_result is not None
        assert len(step.on_result.conditions) == 6

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "assess"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "assess"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "assess"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "assess"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.error"
        assert cond4.route == "release_issue_failure"

        cond5 = step.on_result.conditions[5]
        assert cond5.when is None
        assert cond5.route == "next_or_done"

    def test_investigate_first_merge_step_captures_worktree_path(self) -> None:
        """The merge step captures worktree_path from result.worktree_path."""
        step = self.if_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"]

    def test_implementation_pipeline_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in implementation.yaml has predicate on_result."""
        step = self.ip_recipe.steps["merge"]
        assert step.on_result is not None
        assert len(step.on_result.conditions) == 6

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "fix"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "fix"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "fix"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "fix"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.error"
        assert cond4.route == "release_issue_failure"

        cond5 = step.on_result.conditions[5]
        assert cond5.when is None
        assert cond5.route == "next_or_done"

    def test_implementation_pipeline_merge_step_captures_worktree_path(self) -> None:
        """The merge step in implementation.yaml captures worktree_path."""
        step = self.ip_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"]

    def test_both_recipes_validate_cleanly(self) -> None:
        """Both recipes have no structural errors after predicate routing changes."""
        from autoskillit.recipe.validator import validate_recipe

        if_errors = validate_recipe(self.if_recipe)
        assert if_errors == [], f"remediation.yaml has validation errors: {if_errors}"

        ip_errors = validate_recipe(self.ip_recipe)
        assert ip_errors == [], f"implementation.yaml has validation errors: {ip_errors}"

    def test_both_recipes_no_error_semantic_findings(self) -> None:
        """Both recipes pass semantic rules with no ERROR-severity findings."""
        for recipe, name in [
            (self.if_recipe, "remediation"),
            (self.ip_recipe, "implementation"),
        ]:
            findings = run_semantic_rules(recipe)
            errors = [
                f
                for f in findings
                if f.severity == Severity.ERROR and f.rule != _NO_AUTOSKILLIT_IMPORT
            ]
            assert errors == [], f"{name} has ERROR-severity semantic findings: " + str(
                [(f.rule, f.step_name, f.message) for f in errors]
            )
