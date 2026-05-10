from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchRecipesAuditImpl:
    @pytest.fixture(params=["research.yaml", "research-implement.yaml"])
    def recipe(self, request):
        return load_recipe(builtin_recipes_dir() / request.param)

    def test_has_audit_ingredient_defaulting_true(self, recipe) -> None:
        assert "audit" in recipe.ingredients
        assert recipe.ingredients["audit"].default == "true"

    def test_has_audit_impl_step(self, recipe) -> None:
        assert "audit_impl" in recipe.steps

    def test_audit_impl_skip_when_false(self, recipe) -> None:
        step = recipe.steps["audit_impl"]
        assert step.skip_when_false == "inputs.audit"

    def test_audit_impl_on_context_limit_escalates(self, recipe) -> None:
        """audit_impl on_context_limit must route to escalate_stop."""
        step = recipe.steps["audit_impl"]
        assert step.on_context_limit == "escalate_stop"

    def test_audit_impl_on_failure_escalates(self, recipe) -> None:
        """audit_impl on_failure must route to escalate_stop."""
        step = recipe.steps["audit_impl"]
        assert step.on_failure == "escalate_stop"

    def test_audit_impl_uses_impl_base_sha(self, recipe) -> None:
        """audit_impl skill_command must reference context.impl_base_sha."""
        step = recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.impl_base_sha" in skill_cmd

    def test_audit_impl_uses_group_files(self, recipe) -> None:
        """audit_impl skill_command must reference context.group_files."""
        step = recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.group_files" in skill_cmd

    def test_audit_impl_go_routes_to_run_experiment(self, recipe) -> None:
        """audit_impl on_result GO verdict must route to run_experiment."""
        step = recipe.steps["audit_impl"]
        assert step.on_result is not None
        conditions = step.on_result.conditions
        go_cond = next((c for c in conditions if c.when and "== GO" in c.when), None)
        assert go_cond is not None, "audit_impl must have a GO verdict condition"
        assert go_cond.route == "run_experiment"

    def test_implement_phase_on_context_limit_routes_to_audit_impl(self, recipe) -> None:
        """implement_phase on_context_limit must route to audit_impl."""
        step = recipe.steps["implement_phase"]
        assert step.on_context_limit == "audit_impl"

    def test_implement_phase_on_exhausted_routes_to_audit_impl(self, recipe) -> None:
        """implement_phase on_exhausted must route to audit_impl."""
        step = recipe.steps["implement_phase"]
        assert step.on_exhausted == "audit_impl"

    def test_next_phase_or_experiment_fallback_routes_to_audit_impl(self, recipe) -> None:
        """next_phase_or_experiment default route must be audit_impl."""
        step = recipe.steps["next_phase_or_experiment"]
        conditions = step.on_result.conditions
        default_cond = next((c for c in conditions if c.when is None), None)
        assert default_cond is not None, "next_phase_or_experiment must have a default route"
        assert default_cond.route == "audit_impl"

    def test_check_implement_fix_loop_max_exceeded_routes_to_audit_impl(self, recipe) -> None:
        """check_implement_fix_loop max_exceeded condition must route to audit_impl."""
        step = recipe.steps["check_implement_fix_loop"]
        conditions = step.on_result.conditions
        max_exceeded_cond = next(
            (c for c in conditions if c.when and "max_exceeded" in c.when and "== true" in c.when),
            None,
        )
        assert max_exceeded_cond is not None, (
            "check_implement_fix_loop must have a max_exceeded condition"
        )
        assert max_exceeded_cond.route == "audit_impl"

    def test_check_implement_fix_loop_on_failure_routes_to_audit_impl(self, recipe) -> None:
        """check_implement_fix_loop on_failure must route to audit_impl."""
        step = recipe.steps["check_implement_fix_loop"]
        assert step.on_failure == "audit_impl"

    def test_has_capture_impl_base_step(self, recipe) -> None:
        assert "capture_impl_base" in recipe.steps

    def test_capture_impl_base_on_success_routes_to_plan_phase(self, recipe) -> None:
        """capture_impl_base on_success must route to plan_phase."""
        step = recipe.steps["capture_impl_base"]
        assert step.on_success == "plan_phase"

    def test_decompose_phases_routes_to_capture_impl_base(self, recipe) -> None:
        """decompose_phases on_success must route to capture_impl_base."""
        step = recipe.steps["decompose_phases"]
        assert step.on_success == "capture_impl_base"

    def test_validates_clean(self, recipe) -> None:
        errors = validate_recipe_structure(recipe)
        assert errors == [], f"Validation errors: {errors}"


class TestResearchCampaignAuditIngredient:
    """Tests for audit ingredient in research-campaign.yaml."""

    @pytest.fixture(scope="class")
    def campaign(self):
        return load_recipe(builtin_recipes_dir() / "campaigns" / "research-campaign.yaml")

    def test_campaign_has_audit_ingredient(self, campaign) -> None:
        """research-campaign.yaml must declare an audit ingredient."""
        assert "audit" in campaign.ingredients

    def test_campaign_audit_default_true(self, campaign) -> None:
        """research-campaign.yaml audit ingredient must default to 'true'."""
        assert campaign.ingredients["audit"].default == "true"

    def test_campaign_passes_audit_to_run_implement(self, campaign) -> None:
        """run-implement dispatch must pass audit: "${{ inputs.audit }}"."""
        run_implement = next(d for d in campaign.dispatches if d.name == "run-implement")
        assert "audit" in run_implement.ingredients
        assert run_implement.ingredients["audit"] == "${{ inputs.audit }}"
