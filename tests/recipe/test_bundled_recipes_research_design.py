from __future__ import annotations

import re

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchDesignRecipeStructure:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research-design.yaml")

    def test_loads_without_exception(self, recipe) -> None:
        assert recipe is not None

    def test_validates_with_zero_errors(self, recipe) -> None:
        errors = validate_recipe_structure(recipe)
        assert errors == [], f"Validation errors: {errors}"

    def test_recipe_name(self, recipe) -> None:
        assert recipe.name == "research-design"

    def test_recipe_version(self, recipe) -> None:
        assert recipe.recipe_version == "1.0.0"

    def test_categories(self, recipe) -> None:
        assert recipe.categories == ["research-family"]

    def test_requires_packs(self, recipe) -> None:
        assert recipe.requires_packs == ["research", "vis-lens"]

    def test_ingredient_count(self, recipe) -> None:
        assert len(recipe.ingredients) == 6

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
        assert len(recipe.steps) == 17

    def test_step_names(self, recipe) -> None:
        expected = {
            "scope",
            "select_directions",
            "plan_experiment",
            "dial",
            "select_review_dimensions",
            "apply",
            "synthesize",
            "vis_dial",
            "vis_apply",
            "vis_synthesize",
            "create_worktree",
            "revise_design",
            "check_design_review_loop",
            "resolve_design_review",
            "design_rejected",
            "design_complete",
            "escalate_stop",
        }
        assert set(recipe.steps.keys()) == expected

    def test_scope_routing(self, recipe) -> None:
        step = recipe.steps["scope"]
        assert step.on_success == "select_directions"
        assert step.on_failure == "escalate_stop"

    def test_scope_captures_scope_report(self, recipe) -> None:
        assert "scope_report" in recipe.steps["scope"].capture

    def test_scope_captures_scope_directions(self, recipe) -> None:
        assert "scope_directions" in recipe.steps["scope"].capture

    def test_plan_experiment_routing(self, recipe) -> None:
        step = recipe.steps["plan_experiment"]
        assert step.on_success == "dial"
        assert step.on_failure == "escalate_stop"

    def test_plan_experiment_captures_experiment_plan(self, recipe) -> None:
        assert "experiment_plan" in recipe.steps["plan_experiment"].capture

    def test_plan_experiment_optional_context_refs(self, recipe) -> None:
        refs = recipe.steps["plan_experiment"].optional_context_refs
        for field in ("revision_guidance", "selected_directions"):
            assert field in refs, f"plan_experiment optional_context_refs must include: {field}"

    # ----- dial step tests (T7-T11) -----

    def test_dial_phoropter_family(self, recipe) -> None:
        assert recipe.steps["dial"].phoropter_family == "review-design"

    def test_dial_skip_behavior(self, recipe) -> None:
        assert recipe.steps["dial"].skip_when_false == "inputs.review_design"

    def test_dial_on_success(self, recipe) -> None:
        assert recipe.steps["dial"].on_success == "select_review_dimensions"

    def test_dial_on_failure(self, recipe) -> None:
        assert recipe.steps["dial"].on_failure == "escalate_stop"

    def test_dial_captures(self, recipe) -> None:
        capture = recipe.steps["dial"].capture
        for key in ("experiment_type", "is_silent_type", "classification_timestamp"):
            assert key in capture, f"dial missing capture key: {key}"

    # ----- select_review_dimensions step tests (T12-T13) -----

    def test_select_review_dimensions_phoropter_family(self, recipe) -> None:
        assert recipe.steps["select_review_dimensions"].phoropter_family == "review-design"

    def test_select_review_dimensions_captures(self, recipe) -> None:
        capture = recipe.steps["select_review_dimensions"].capture
        for key in ("selected_lenses", "dimensions_manifest_path"):
            assert key in capture, f"select_review_dimensions missing capture key: {key}"

    # ----- apply step tests (T14-T21) -----

    def test_apply_phoropter_family(self, recipe) -> None:
        assert recipe.steps["apply"].phoropter_family == "review-design"

    def test_apply_skip_when_true(self, recipe) -> None:
        assert recipe.steps["apply"].skip_when_true == "context.is_silent_type"

    def test_apply_retries(self, recipe) -> None:
        assert recipe.steps["apply"].retries == 2

    def test_apply_on_exhausted(self, recipe) -> None:
        assert recipe.steps["apply"].on_exhausted == "synthesize"

    def test_apply_on_context_limit(self, recipe) -> None:
        assert recipe.steps["apply"].on_context_limit == "synthesize"

    def test_apply_on_failure(self, recipe) -> None:
        assert recipe.steps["apply"].on_failure == "synthesize"

    def test_apply_receives_scope_report(self, recipe) -> None:
        """apply step must pass scope_report as a second argument."""
        step = recipe.steps["apply"]
        cmd = step.with_args["skill_command"]
        assert "${{ context.scope_report }}" in cmd

    def test_apply_captures(self, recipe) -> None:
        capture = recipe.steps["apply"].capture
        for key in ("findings_manifest_path", "evaluation_dashboard"):
            assert key in capture, f"apply missing capture key: {key}"

    # ----- synthesize step tests (T22-T28) -----

    def test_synthesize_phoropter_family(self, recipe) -> None:
        assert recipe.steps["synthesize"].phoropter_family == "review-design"

    def test_synthesize_on_result_go(self, recipe) -> None:
        step = recipe.steps["synthesize"]
        assert step.on_result is not None
        go_cond = next((c for c in step.on_result.conditions if c.when and "GO" in c.when), None)
        assert go_cond is not None, "Missing GO route"
        assert go_cond.route == "vis_dial"

    def test_synthesize_on_result_revise(self, recipe) -> None:
        step = recipe.steps["synthesize"]
        assert step.on_result is not None
        revise_cond = next(
            (c for c in step.on_result.conditions if c.when and "REVISE" in c.when), None
        )
        assert revise_cond is not None, "Missing REVISE route"
        assert revise_cond.route == "revise_design"

    def test_synthesize_on_result_stop(self, recipe) -> None:
        step = recipe.steps["synthesize"]
        assert step.on_result is not None
        stop_cond = next(
            (c for c in step.on_result.conditions if c.when and "STOP" in c.when), None
        )
        assert stop_cond is not None, "Missing STOP route"
        assert stop_cond.route == "resolve_design_review"

    def test_synthesize_on_result_fallback(self, recipe) -> None:
        step = recipe.steps["synthesize"]
        assert step.on_result is not None
        fallback = next((c for c in step.on_result.conditions if c.when is None), None)
        assert fallback is not None, "Missing fallback route"
        assert fallback.route == "vis_dial"

    def test_synthesize_no_on_success(self, recipe) -> None:
        assert recipe.steps["synthesize"].on_success is None

    def test_synthesize_captures(self, recipe) -> None:
        capture = recipe.steps["synthesize"].capture
        for key in ("verdict", "revision_guidance"):
            assert key in capture, f"synthesize missing capture key: {key}"

    # ----- vis-lens renamed step tests (T29-T37) -----

    def test_vis_dial_phoropter_family(self, recipe) -> None:
        assert recipe.steps["vis_dial"].phoropter_family is None

    def test_vis_dial_on_success(self, recipe) -> None:
        assert recipe.steps["vis_dial"].on_success == "vis_apply"

    def test_vis_dial_on_failure(self, recipe) -> None:
        assert recipe.steps["vis_dial"].on_failure == "escalate_stop"

    def test_vis_dial_captures(self, recipe) -> None:
        capture = recipe.steps["vis_dial"].capture
        for key in ("selected_lenses", "lens_context_paths"):
            assert key in capture, f"vis_dial missing capture key: {key}"

    def test_vis_apply_phoropter_family(self, recipe) -> None:
        assert recipe.steps["vis_apply"].phoropter_family is None

    def test_vis_apply_capture_list(self, recipe) -> None:
        assert "all_figure_spec_paths" in recipe.steps["vis_apply"].capture_list

    def test_vis_apply_retries_zero(self, recipe) -> None:
        assert recipe.steps["vis_apply"].retries == 0

    def test_vis_apply_on_success(self, recipe) -> None:
        assert recipe.steps["vis_apply"].on_success == "vis_synthesize"

    def test_vis_apply_on_failure(self, recipe) -> None:
        assert recipe.steps["vis_apply"].on_failure == "vis_synthesize"

    def test_vis_synthesize_phoropter_family(self, recipe) -> None:
        assert recipe.steps["vis_synthesize"].phoropter_family is None

    def test_vis_synthesize_on_success(self, recipe) -> None:
        assert recipe.steps["vis_synthesize"].on_success == "create_worktree"

    def test_vis_synthesize_on_failure(self, recipe) -> None:
        assert recipe.steps["vis_synthesize"].on_failure == "escalate_stop"

    def test_vis_synthesize_captures(self, recipe) -> None:
        capture = recipe.steps["vis_synthesize"].capture
        for key in (
            "visualization_plan_path",
            "report_plan_path",
            "visualization_plan_trace_path",
        ):
            assert key in capture, f"vis_synthesize missing capture key: {key}"

    def test_no_plan_visualization_step(self, recipe) -> None:
        assert "plan_visualization" not in recipe.steps

    def test_revise_design_is_route_action(self, recipe) -> None:
        assert recipe.steps["revise_design"].action == "route"

    def test_revise_design_routes_to_check_design_review_loop(self, recipe) -> None:
        step = recipe.steps["revise_design"]
        assert step.on_result is not None
        default = next((c for c in step.on_result.conditions if c.when is None), None)
        assert default is not None
        assert default.route == "check_design_review_loop"

    # ----- check_design_review_loop (T38) -----

    def test_check_design_review_loop_non_exhausted_routes_to_apply(self, recipe) -> None:
        step = recipe.steps["check_design_review_loop"]
        assert step.on_result is not None
        default = next((c for c in step.on_result.conditions if c.when is None), None)
        assert default is not None
        assert default.route == "apply"

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
            "scope_directions",
            "selected_directions",
            "experiment_plan",
            "visualization_plan_path",
            "report_plan_path",
            "experiment_type",
            "worktree_path",
            "research_dir",
        ):
            assert field in message, (
                f"design_complete message must mention sentinel field: {field}"
            )

    def test_design_has_select_directions_step(self, recipe) -> None:
        assert "select_directions" in recipe.steps

    def test_design_scope_routes_to_select_directions(self, recipe) -> None:
        assert recipe.steps["scope"].on_success == "select_directions"

    def test_design_select_directions_routes_to_plan_experiment(self, recipe) -> None:
        assert recipe.steps["select_directions"].on_success == "plan_experiment"

    def test_design_select_directions_captures_selected_directions(self, recipe) -> None:
        assert "selected_directions" in recipe.steps["select_directions"].capture

    def test_design_complete_sentinel_includes_selected_directions(self, recipe) -> None:
        msg = recipe.steps["design_complete"].message
        assert re.search(r"selected_directions\s*=\s*<context\.selected_directions>", msg), (
            "design_complete message must reference selected_directions via template syntax"
        )

    def test_design_has_min_breadth_ingredient(self, recipe) -> None:
        assert "min_breadth" in recipe.ingredients
        assert recipe.ingredients["min_breadth"].default == "2"

    def test_design_complete_sentinel_includes_scope_directions(self, recipe) -> None:
        msg = recipe.steps["design_complete"].message
        assert re.search(r"scope_directions\s*=\s*<context\.scope_directions>", msg), (
            "design_complete message must reference scope_directions via template syntax"
        )

    def test_design_recipe_has_create_worktree_step(self, recipe) -> None:
        step = recipe.steps["create_worktree"]
        assert step.tool == "run_cmd"
        assert "worktree_path" in step.capture
        assert "research_dir" in step.capture
        assert step.on_success == "design_complete"

    def test_design_sentinel_emits_worktree_fields(self, recipe) -> None:
        sentinel = recipe.steps["design_complete"]
        assert "worktree_path" in sentinel.message
        assert "research_dir" in sentinel.message

    def test_escalate_stop_is_stop(self, recipe) -> None:
        step = recipe.steps["escalate_stop"]
        assert step.action == "stop"
        assert step.message, "escalate_stop must have a non-empty message"

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
