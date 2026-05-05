from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchDesignRecipeStructure:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research-design.yaml")

    def test_loads_without_exception(self, recipe) -> None:
        assert recipe is not None

    def test_validates_with_zero_errors(self, recipe) -> None:
        errors = validate_recipe(recipe)
        assert errors == [], f"Validation errors: {errors}"

    def test_recipe_name(self, recipe) -> None:
        assert recipe.name == "research-design"

    def test_recipe_version(self, recipe) -> None:
        assert recipe.recipe_version == "1.0.0"

    def test_categories(self, recipe) -> None:
        assert recipe.categories == ["research-family"]

    def test_requires_packs(self, recipe) -> None:
        assert recipe.requires_packs == ["research"]

    def test_ingredient_count(self, recipe) -> None:
        assert len(recipe.ingredients) == 5

    def test_task_ingredient_required(self, recipe) -> None:
        assert "task" in recipe.ingredients
        assert recipe.ingredients["task"].required is True

    def test_source_dir_ingredient_required(self, recipe) -> None:
        assert "source_dir" in recipe.ingredients
        assert recipe.ingredients["source_dir"].required is True

    def test_base_branch_ingredient_default(self, recipe) -> None:
        assert "base_branch" in recipe.ingredients
        assert recipe.ingredients["base_branch"].default == "main"

    def test_review_design_ingredient_default(self, recipe) -> None:
        assert "review_design" in recipe.ingredients
        assert recipe.ingredients["review_design"].default == "true"

    def test_issue_url_ingredient_optional(self, recipe) -> None:
        assert "issue_url" in recipe.ingredients
        assert recipe.ingredients["issue_url"].required is False

    def test_step_count(self, recipe) -> None:
        assert len(recipe.steps) == 9

    def test_step_names(self, recipe) -> None:
        expected = {
            "scope",
            "plan_experiment",
            "review_design",
            "plan_visualization",
            "revise_design",
            "resolve_design_review",
            "design_rejected",
            "design_complete",
            "escalate_stop",
        }
        assert set(recipe.steps.keys()) == expected

    def test_scope_routing(self, recipe) -> None:
        step = recipe.steps["scope"]
        assert step.on_success == "plan_experiment"
        assert step.on_failure == "escalate_stop"

    def test_scope_captures_scope_report(self, recipe) -> None:
        assert "scope_report" in recipe.steps["scope"].capture

    def test_plan_experiment_routing(self, recipe) -> None:
        step = recipe.steps["plan_experiment"]
        assert step.on_success == "review_design"
        assert step.on_failure == "escalate_stop"

    def test_plan_experiment_captures_experiment_plan(self, recipe) -> None:
        assert "experiment_plan" in recipe.steps["plan_experiment"].capture

    def test_plan_experiment_optional_context_refs(self, recipe) -> None:
        assert "revision_guidance" in recipe.steps["plan_experiment"].optional_context_refs

    def test_review_design_skip_when_false(self, recipe) -> None:
        assert recipe.steps["review_design"].skip_when_false == "inputs.review_design"

    def test_review_design_retries(self, recipe) -> None:
        assert recipe.steps["review_design"].retries == 2

    def test_review_design_on_exhausted(self, recipe) -> None:
        assert recipe.steps["review_design"].on_exhausted == "plan_visualization"

    def test_review_design_on_context_limit(self, recipe) -> None:
        assert recipe.steps["review_design"].on_context_limit == "plan_visualization"

    def test_review_design_on_failure(self, recipe) -> None:
        assert recipe.steps["review_design"].on_failure == "plan_visualization"

    def test_review_design_on_result_go(self, recipe) -> None:
        step = recipe.steps["review_design"]
        assert step.on_result is not None
        go_cond = next((c for c in step.on_result.conditions if c.when and "GO" in c.when), None)
        assert go_cond is not None, "Missing GO route"
        assert go_cond.route == "plan_visualization"

    def test_review_design_on_result_revise(self, recipe) -> None:
        step = recipe.steps["review_design"]
        assert step.on_result is not None
        revise_cond = next(
            (c for c in step.on_result.conditions if c.when and "REVISE" in c.when), None
        )
        assert revise_cond is not None, "Missing REVISE route"
        assert revise_cond.route == "revise_design"

    def test_review_design_on_result_stop(self, recipe) -> None:
        step = recipe.steps["review_design"]
        assert step.on_result is not None
        stop_cond = next(
            (c for c in step.on_result.conditions if c.when and "STOP" in c.when), None
        )
        assert stop_cond is not None, "Missing STOP route"
        assert stop_cond.route == "resolve_design_review"

    def test_review_design_on_result_fallback(self, recipe) -> None:
        step = recipe.steps["review_design"]
        assert step.on_result is not None
        fallback = next((c for c in step.on_result.conditions if c.when is None), None)
        assert fallback is not None, "Missing fallback route"
        assert fallback.route == "plan_visualization"

    def test_review_design_no_on_success(self, recipe) -> None:
        assert recipe.steps["review_design"].on_success is None

    def test_review_design_captures(self, recipe) -> None:
        capture = recipe.steps["review_design"].capture
        for key in ("verdict", "experiment_type", "evaluation_dashboard", "revision_guidance"):
            assert key in capture, f"Missing capture key: {key}"

    def test_plan_visualization_on_success(self, recipe) -> None:
        assert recipe.steps["plan_visualization"].on_success == "design_complete"

    def test_plan_visualization_on_failure(self, recipe) -> None:
        assert recipe.steps["plan_visualization"].on_failure == "escalate_stop"

    def test_plan_visualization_captures(self, recipe) -> None:
        capture = recipe.steps["plan_visualization"].capture
        assert "visualization_plan_path" in capture
        assert "report_plan_path" in capture

    def test_revise_design_is_route_action(self, recipe) -> None:
        assert recipe.steps["revise_design"].action == "route"

    def test_revise_design_routes_to_plan_experiment(self, recipe) -> None:
        step = recipe.steps["revise_design"]
        assert step.on_result is not None
        default = next((c for c in step.on_result.conditions if c.when is None), None)
        assert default is not None
        assert default.route == "plan_experiment"

    def test_resolve_design_review_retries(self, recipe) -> None:
        assert recipe.steps["resolve_design_review"].retries == 1

    def test_resolve_design_review_on_failure(self, recipe) -> None:
        assert recipe.steps["resolve_design_review"].on_failure == "design_rejected"

    def test_resolve_design_review_on_context_limit(self, recipe) -> None:
        assert recipe.steps["resolve_design_review"].on_context_limit == "design_rejected"

    def test_resolve_design_review_on_result_revised(self, recipe) -> None:
        step = recipe.steps["resolve_design_review"]
        assert step.on_result is not None
        revised = next(
            (c for c in step.on_result.conditions if c.when and "revised" in c.when), None
        )
        assert revised is not None, "Missing revised route"
        assert revised.route == "revise_design"

    def test_resolve_design_review_on_result_failed(self, recipe) -> None:
        step = recipe.steps["resolve_design_review"]
        assert step.on_result is not None
        failed = next(
            (c for c in step.on_result.conditions if c.when and "failed" in c.when), None
        )
        assert failed is not None, "Missing failed route"
        assert failed.route == "design_rejected"

    def test_resolve_design_review_fallback(self, recipe) -> None:
        step = recipe.steps["resolve_design_review"]
        assert step.on_result is not None
        fallback = next((c for c in step.on_result.conditions if c.when is None), None)
        assert fallback is not None, "Missing fallback route"
        assert fallback.route == "design_rejected"

    def test_resolve_design_review_captures_revision_guidance(self, recipe) -> None:
        assert "revision_guidance" in recipe.steps["resolve_design_review"].capture

    def test_design_rejected_is_stop(self, recipe) -> None:
        step = recipe.steps["design_rejected"]
        assert step.action == "stop"
        assert step.message, "design_rejected must have a non-empty message"

    def test_design_complete_is_stop(self, recipe) -> None:
        step = recipe.steps["design_complete"]
        assert step.action == "stop"
        assert step.message, "design_complete must have a non-empty message"

    def test_design_complete_sentinel_fields(self, recipe) -> None:
        message = recipe.steps["design_complete"].message
        for field in (
            "scope_report",
            "experiment_plan",
            "visualization_plan_path",
            "report_plan_path",
            "experiment_type",
        ):
            assert field in message, (
                f"design_complete message must mention sentinel field: {field}"
            )

    def test_escalate_stop_is_stop(self, recipe) -> None:
        step = recipe.steps["escalate_stop"]
        assert step.action == "stop"
        assert step.message, "escalate_stop must have a non-empty message"

    def test_no_dangling_create_worktree(self, recipe) -> None:
        for name, step in recipe.steps.items():
            assert "create_worktree" not in name, f"Step name '{name}' references create_worktree"
            for attr in (
                step.on_success,
                step.on_failure,
                step.on_exhausted,
                step.on_context_limit,
            ):
                if attr:
                    assert "create_worktree" not in attr, (
                        f"Step '{name}' routes to create_worktree via {attr!r}"
                    )
            if step.on_result:
                for cond in step.on_result.conditions:
                    assert "create_worktree" not in cond.route, (
                        f"Step '{name}' on_result routes to create_worktree"
                    )

    def test_kitchen_rules_count(self, recipe) -> None:
        assert len(recipe.kitchen_rules) == 2

    def test_kitchen_rule_no_native_tools(self, recipe) -> None:
        rule = recipe.kitchen_rules[0]
        for tool in ("Read", "Grep", "Glob", "Edit", "Write", "Bash"):
            assert tool in rule, f"Kitchen rule 1 must forbid native tool: {tool}"

    def test_kitchen_rule_food_truck_sentinel(self, recipe) -> None:
        rule = recipe.kitchen_rules[1].lower()
        assert "food truck" in rule, "Kitchen rule 2 must mention food truck"
        assert "sentinel" in rule, "Kitchen rule 2 must mention sentinel emission"
