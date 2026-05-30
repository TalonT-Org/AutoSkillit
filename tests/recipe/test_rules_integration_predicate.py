from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules

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
        assert len(step.on_result.conditions) == 8

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "check_merge_fix_loop"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "check_merge_fix_loop"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "check_merge_fix_loop"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "check_merge_rebase_loop"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.failed_step == 'dirty_main_repo'"
        assert cond4.route == "check_dirty_main_retry"

        cond5 = step.on_result.conditions[5]
        assert cond5.when == "result.failed_step == 'ref_coherence'"
        assert cond5.route == "check_ref_push_loop"

        cond6 = step.on_result.conditions[6]
        assert cond6.when == "result.error"
        assert cond6.route == "release_issue_failure"

        cond7 = step.on_result.conditions[7]
        assert cond7.when is None
        assert cond7.route == "inter_part_push"

    def test_investigate_first_merge_step_captures_worktree_path(self) -> None:
        """The merge step captures worktree_path from result.worktree_path."""
        step = self.if_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"].from_

    def test_implementation_pipeline_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in implementation.yaml has predicate on_result."""
        step = self.ip_recipe.steps["merge"]
        assert step.on_result is not None
        assert len(step.on_result.conditions) == 8

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "check_merge_fix_loop"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "check_merge_fix_loop"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "check_merge_fix_loop"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "check_merge_rebase_loop"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.failed_step == 'dirty_main_repo'"
        assert cond4.route == "check_dirty_main_retry"

        cond5 = step.on_result.conditions[5]
        assert cond5.when == "result.failed_step == 'ref_coherence'"
        assert cond5.route == "check_ref_push_loop"

        cond6 = step.on_result.conditions[6]
        assert cond6.when == "result.error"
        assert cond6.route == "release_issue_failure"

        cond7 = step.on_result.conditions[7]
        assert cond7.when is None
        assert cond7.route == "inter_part_push"

    def test_implementation_pipeline_merge_step_captures_worktree_path(self) -> None:
        """The merge step in implementation.yaml captures worktree_path."""
        step = self.ip_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"].from_

    def test_both_recipes_validate_cleanly(self) -> None:
        """Both recipes have no structural errors after predicate routing changes."""

        from autoskillit.recipe.validator import validate_recipe_structure

        if_errors = validate_recipe_structure(self.if_recipe)
        assert if_errors == [], f"remediation.yaml has validation errors: {if_errors}"

        ip_errors = validate_recipe_structure(self.ip_recipe)
        assert ip_errors == [], f"implementation.yaml has validation errors: {ip_errors}"

    def test_both_recipes_no_error_semantic_findings(self) -> None:
        """Both recipes pass semantic rules with no ERROR-severity findings."""
        for recipe, name in [
            (self.if_recipe, "remediation"),
            (self.ip_recipe, "implementation"),
        ]:
            findings = run_semantic_rules(recipe)
            errors = [f for f in findings if f.severity == Severity.ERROR]
            assert errors == [], f"{name} has ERROR-severity semantic findings: " + str(
                [(f.rule, f.step_name, f.message) for f in errors]
            )


class TestLoopBudgetSeparation:
    """Budget separation: merge-fix and audit-remediation use independent counters."""

    RECIPE_NAMES = ["remediation", "implementation", "implementation-groups"]

    @pytest.fixture(scope="class", autouse=True)
    def _load_recipes(self, request) -> None:
        request.cls.recipes = {
            name: load_recipe(builtin_recipes_dir() / f"{name}.yaml")
            for name in TestLoopBudgetSeparation.RECIPE_NAMES
        }

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_test_step_bypasses_merge_fix_guard(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        assert recipe.steps["test"].on_success != "check_merge_fix_loop"

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_audit_remediation_loop_exists_and_wired(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        step = recipe.steps["check_audit_remediation_loop"]
        assert step.tool == "run_python"
        assert step.with_args["callable"] == "autoskillit.smoke_utils.check_loop_iteration"
        assert "audit_remediation_count" in step.capture
        exceeded = [c for c in step.on_result.conditions if c.when and "max_exceeded" in c.when]
        assert any(c.route == "release_issue_failure" for c in exceeded)

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_audit_impl_no_go_routes_to_audit_loop(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        audit_step = recipe.steps["audit_impl"]
        fallthrough = [
            c.route
            for c in audit_step.on_result.conditions
            if c.when is None or ("GO" not in c.when and "error" not in c.when)
        ]
        assert fallthrough == ["check_audit_remediation_loop"]

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_all_merge_failure_arms_guarded(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        merge_step = recipe.steps["merge"]
        merge_fix_guard_steps = {
            "check_merge_fix_loop",
            "check_merge_rebase_loop",
            "check_dirty_main_retry",
        }
        guard_steps = merge_fix_guard_steps | {"check_ref_push_loop"}
        for cond in merge_step.on_result.conditions:
            if cond.when and "failed_step" in cond.when:
                assert cond.route in guard_steps, (
                    f"{cond.when} routes to {cond.route}, expected a guard step"
                )
        for name in merge_fix_guard_steps:
            step = recipe.steps[name]
            assert step.with_args.get("current_iteration") == "${{ context.merge_fix_count }}"

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_loop_budget_ingredients_exist(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        assert "merge_fix_max_retries" in recipe.ingredients
        assert recipe.ingredients["merge_fix_max_retries"].default == "3"
        assert "audit_remediation_max_retries" in recipe.ingredients
        assert recipe.ingredients["audit_remediation_max_retries"].default == "2"
        assert "test_fix_max_retries" in recipe.ingredients
        assert recipe.ingredients["test_fix_max_retries"].default == "3"
        assert recipe.ingredients["test_fix_max_retries"].hidden is True
        assert "merge_test_fix_max_retries" in recipe.ingredients
        assert recipe.ingredients["merge_test_fix_max_retries"].default == "3"
        assert recipe.ingredients["merge_test_fix_max_retries"].hidden is True

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_guard_steps_use_ingredients(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        for name in (
            "check_merge_fix_loop",
            "check_merge_rebase_loop",
            "check_dirty_main_retry",
        ):
            step = recipe.steps[name]
            assert step.with_args["max_iterations"] == "${{ inputs.merge_fix_max_retries }}"
        audit_guard = recipe.steps["check_audit_remediation_loop"]
        assert (
            audit_guard.with_args["max_iterations"]
            == "${{ inputs.audit_remediation_max_retries }}"
        )
        test_fix_guard = recipe.steps["check_test_fix_loop"]
        assert test_fix_guard.with_args["max_iterations"] == "${{ inputs.test_fix_max_retries }}"
        merge_test_fix_guard = recipe.steps["check_merge_test_fix_loop"]
        assert (
            merge_test_fix_guard.with_args["max_iterations"]
            == "${{ inputs.merge_test_fix_max_retries }}"
        )

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_merge_test_fix_loop_uses_separate_counter(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        step = recipe.steps["check_merge_test_fix_loop"]
        assert step.with_args["current_iteration"] == "${{ context.merge_test_fix_loop_count }}"
        assert "merge_test_fix_loop_count" in step.capture
