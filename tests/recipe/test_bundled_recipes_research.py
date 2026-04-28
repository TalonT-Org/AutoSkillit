"""Tests for research.yaml bundled recipe structure and archival steps."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchRecipeStructure:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research.yaml")

    def test_research_has_review_pr_ingredient(self, recipe) -> None:
        """research.yaml must declare a review_pr ingredient with default 'false'."""
        assert "review_pr" in recipe.ingredients, (
            "research.yaml must declare a 'review_pr' ingredient to gate the optional "
            "review-research-pr step"
        )
        assert recipe.ingredients["review_pr"].default == "false"

    def test_research_has_review_research_pr_step(self, recipe) -> None:
        """research.yaml must include a review_research_pr step."""
        assert "review_research_pr" in recipe.steps

    def test_research_review_step_skip_when_false(self, recipe) -> None:
        """review_research_pr step must use skip_when_false: inputs.review_pr."""
        step = recipe.steps["review_research_pr"]
        assert step.skip_when_false == "inputs.review_pr"

    def test_research_no_issue_number_ingredient(self, recipe) -> None:
        """Removed: issue_number is the phase-2 gate."""
        assert "issue_number" not in recipe.ingredients

    def test_research_no_setup_phases_ingredient(self, recipe) -> None:
        """Removed: setup_phases toggle replaced by always-decompose."""
        assert "setup_phases" not in recipe.ingredients

    def test_research_no_check_phase_step(self, recipe) -> None:
        assert "check_phase" not in recipe.steps

    def test_research_no_phase1_done_step(self, recipe) -> None:
        assert "phase1_done" not in recipe.steps

    def test_research_no_open_plan_issue_step(self, recipe) -> None:
        assert "open_plan_issue" not in recipe.steps

    def test_research_no_save_experiment_plan_step(self, recipe) -> None:
        assert "save_experiment_plan" not in recipe.steps

    def test_research_no_check_setup_needed_step(self, recipe) -> None:
        assert "check_setup_needed" not in recipe.steps

    def test_research_no_implement_experiment_step(self, recipe) -> None:
        assert "implement_experiment" not in recipe.steps

    def test_research_has_issue_url_ingredient(self, recipe) -> None:
        assert "issue_url" in recipe.ingredients
        assert not recipe.ingredients["issue_url"].required

    def test_research_has_review_design_ingredient(self, recipe) -> None:
        assert "review_design" in recipe.ingredients
        assert recipe.ingredients["review_design"].default == "true"

    def test_research_has_review_design_step(self, recipe) -> None:
        assert "review_design" in recipe.steps

    def test_review_design_step_skip_when_false(self, recipe) -> None:
        step = recipe.steps["review_design"]
        assert step.skip_when_false == "inputs.review_design"

    def test_review_design_step_retries_and_exhausted(self, recipe) -> None:
        step = recipe.steps["review_design"]
        assert step.retries == 2
        assert step.on_exhausted == "create_worktree"

    def test_plan_experiment_routes_to_review_design(self, recipe) -> None:
        step = recipe.steps["plan_experiment"]
        assert step.on_success == "review_design"

    def test_review_design_on_result_routing(self, recipe) -> None:
        """review_design STOP verdict routes to resolve_design_review (not design_rejected)."""
        step = recipe.steps["review_design"]
        assert step.on_result is not None
        go_cond = next((c for c in step.on_result.conditions if c.when and "GO" in c.when), None)
        assert go_cond is not None, "Missing GO route"
        assert go_cond.route == "plan_visualization"
        revise_cond = next(
            (c for c in step.on_result.conditions if c.when and "REVISE" in c.when), None
        )
        assert revise_cond is not None, "Missing REVISE route"
        assert revise_cond.route == "revise_design"
        stop_cond = next(
            (c for c in step.on_result.conditions if c.when and "STOP" in c.when), None
        )
        assert stop_cond is not None, "Missing STOP route"
        assert stop_cond.route == "resolve_design_review"

    def test_has_revise_design_step(self, recipe) -> None:
        assert "revise_design" in recipe.steps

    def test_has_design_rejected_step(self, recipe) -> None:
        assert "design_rejected" in recipe.steps

    def test_design_rejected_step_is_action_stop(self, recipe) -> None:
        """design_rejected must halt the pipeline (action=stop) with an explanatory message.

        Regression guard: changing action to 'route' would silently break
        the hard-halt guarantee for fundamentally flawed designs.
        """
        step = recipe.steps["design_rejected"]
        assert step.action == "stop"
        assert step.message, "design_rejected must have a non-empty message"
        assert "STOP" in step.message, (
            "design_rejected message must reference the STOP verdict for clarity."
        )

    def test_kitchen_rule_6_acknowledges_stop_verdict_exception(self, recipe) -> None:
        """Kitchen rule #6 must scope 'do not stop' to exhaustion/failure, not STOP verdict.

        The current blanket 'do not stop' language contradicts the design_rejected
        hard halt. The rule must explicitly note that verdict=STOP is the sole exception.
        """
        rule6 = recipe.kitchen_rules[5]  # 0-indexed; rule #6 is index 5
        rule6_lower = rule6.lower()
        assert "stop" in rule6_lower, (
            "Kitchen rule #6 must reference 'stop' in the verdict exception context."
        )
        assert "verdict" in rule6_lower or "exception" in rule6_lower, (
            "Kitchen rule #6 must acknowledge that verdict=STOP is an exception to "
            "'do not stop on exhaustion/failure'. Current language is overbroad and "
            "contradicts the design_rejected routing."
        )

    def test_has_resolve_design_review_step(self, recipe) -> None:
        """resolve_design_review step must be present in research.yaml."""
        assert "resolve_design_review" in recipe.steps

    def test_resolve_design_review_routes_revised_to_revise_design(self, recipe) -> None:
        """resolve_design_review routes resolution=revised back to revise_design."""
        step = recipe.steps["resolve_design_review"]
        assert step.on_result is not None
        revised_cond = next(
            (c for c in step.on_result.conditions if c.when and "revised" in c.when), None
        )
        assert revised_cond is not None, "Missing revised route"
        assert revised_cond.route == "revise_design"

    def test_resolve_design_review_routes_failed_to_design_rejected(self, recipe) -> None:
        """resolve_design_review routes resolution=failed to design_rejected."""
        step = recipe.steps["resolve_design_review"]
        assert step.on_result is not None
        failed_cond = next(
            (c for c in step.on_result.conditions if c.when and "failed" in c.when), None
        )
        assert failed_cond is not None, "Missing failed route"
        assert failed_cond.route == "design_rejected"

    def test_resolve_design_review_fallbacks_to_design_rejected(self, recipe) -> None:
        """resolve_design_review routes on_failure and on_context_limit to design_rejected."""
        step = recipe.steps["resolve_design_review"]
        assert step.on_failure == "design_rejected"
        assert step.on_context_limit == "design_rejected"

    def test_resolve_design_review_captures_revision_guidance(self, recipe) -> None:
        """resolve_design_review must capture revision_guidance for the revise_design loop."""
        step = recipe.steps["resolve_design_review"]
        assert "revision_guidance" in step.capture, (
            "resolve_design_review must capture revision_guidance so revise_design → "
            "plan_experiment picks up the triage-generated guidance"
        )

    def test_has_resolve_research_review_step(self, recipe) -> None:
        step = recipe.steps["resolve_research_review"]
        assert step.retries == 2
        assert step.on_exhausted == "route_claims_resolve"
        assert step.on_success == "route_claims_resolve"
        assert step.on_failure == "route_claims_resolve"

    def test_has_re_push_research_step(self, recipe) -> None:
        assert "re_push_research" in recipe.steps

    def test_requires_packs_includes_exp_lens(self, recipe) -> None:
        assert "exp-lens" in recipe.requires_packs
        assert "research" in recipe.requires_packs

    def test_re_run_experiment_step(self, recipe) -> None:
        assert "re_run_experiment" in recipe.steps
        step = recipe.steps["re_run_experiment"]
        assert step.tool == "run_skill"
        assert "--adjust" in step.with_args.get("skill_command", "")
        assert step.on_success == "re_generate_report"

    def test_re_generate_report_step(self, recipe) -> None:
        assert "re_generate_report" in recipe.steps
        step = recipe.steps["re_generate_report"]
        assert step.tool == "run_skill"
        assert step.on_success == "re_stage_bundle"

    def test_re_test_step(self, recipe) -> None:
        assert "re_test" in recipe.steps
        step = recipe.steps["re_test"]
        assert step.tool == "test_check"
        assert step.on_success == "re_push_research"

    def test_revalidation_loop_all_paths_reach_begin_archival(self, recipe) -> None:
        """Every path from merge_escalations reaches begin_archival."""
        for step_name in ("re_run_experiment", "re_generate_report", "re_test"):
            step = recipe.steps[step_name]
            assert step.on_failure in ("begin_archival", "re_push_research")
        assert recipe.steps["re_push_research"].on_success == "finalize_bundle_render"

    def test_audit_claims_ingredient_default_false(self, recipe) -> None:
        assert "audit_claims" in recipe.ingredients
        assert recipe.ingredients["audit_claims"].required is False
        assert recipe.ingredients["audit_claims"].default == "false"

    def test_review_research_pr_captures_review_verdict(self, recipe) -> None:
        step = recipe.steps["review_research_pr"]
        assert "review_verdict" in step.capture
        assert "verdict" not in step.capture  # old key must be gone

    def test_audit_claims_step_routes_to_route_review_resolve(self, recipe) -> None:
        step = recipe.steps["audit_claims"]
        assert step.tool == "run_skill"
        assert "audit_claims" in step.skip_when_false
        # all on_result routes point to route_review_resolve
        routes = {c.route for c in step.on_result.conditions}
        assert routes == {"route_review_resolve"}

    def test_new_routing_steps_exist(self, recipe) -> None:
        assert "route_review_resolve" in recipe.steps
        assert "route_claims_resolve" in recipe.steps
        assert "merge_escalations" in recipe.steps
        assert "check_escalations" not in recipe.steps  # replaced

    def test_route_review_resolve_routing_logic(self, recipe) -> None:
        step = recipe.steps["route_review_resolve"]
        assert step.action == "route"
        conditions = step.on_result.conditions
        # changes_requested → resolve_research_review; else → route_claims_resolve
        guarded = [c for c in conditions if c.when is not None]
        default = [c for c in conditions if c.when is None]
        assert len(guarded) == 1
        assert "changes_requested" in guarded[0].when
        assert guarded[0].route == "resolve_research_review"
        assert len(default) == 1
        assert default[0].route == "route_claims_resolve"

    def test_route_claims_resolve_routing_logic(self, recipe) -> None:
        step = recipe.steps["route_claims_resolve"]
        assert step.action == "route"
        conditions = step.on_result.conditions
        # changes_requested → resolve_claims_review; else → merge_escalations
        guarded = [c for c in conditions if c.when is not None]
        default = [c for c in conditions if c.when is None]
        assert len(guarded) == 1
        assert "changes_requested" in guarded[0].when
        assert guarded[0].route == "resolve_claims_review"
        assert len(default) == 1
        assert default[0].route == "merge_escalations"

    def test_merge_escalations_routing_logic(self, recipe) -> None:
        step = recipe.steps["merge_escalations"]
        assert step.action == "route"
        conditions = step.on_result.conditions
        # review_needs_rerun == true → re_run_experiment
        # claims_needs_rerun == true → re_run_experiment
        # else → finalize_bundle
        guarded = [c for c in conditions if c.when is not None]
        default = [c for c in conditions if c.when is None]
        assert len(guarded) == 2
        rerun_routes = {c.route for c in guarded}
        assert rerun_routes == {"re_run_experiment"}
        assert any("review_needs_rerun" in c.when for c in guarded)
        assert any("claims_needs_rerun" in c.when for c in guarded)
        assert len(default) == 1
        assert default[0].route == "finalize_bundle"

    def test_resolve_claims_review_step_exists(self, recipe) -> None:
        step = recipe.steps["resolve_claims_review"]
        assert "claims_needs_rerun" in step.capture

    def test_resolve_research_review_captures_review_needs_rerun(self, recipe) -> None:
        step = recipe.steps["resolve_research_review"]
        assert "review_needs_rerun" in step.capture
        assert "needs_rerun" not in step.capture  # old key must be gone

    def test_research_recipe_validates_cleanly_with_new_steps(self, recipe) -> None:
        errors = validate_recipe(recipe)
        assert errors == [], f"Validation errors: {errors}"

    # --- Archival phase tests ---

    def test_archival_begin_archival_step_exists(self, recipe) -> None:
        """begin_archival must be a route step gating archival on pr_url."""
        assert "begin_archival" in recipe.steps
        step = recipe.steps["begin_archival"]
        assert step.action == "route"

    def test_archival_begin_archival_routes_to_capture(self, recipe) -> None:
        """begin_archival routes to capture_experiment_branch when pr_url is truthy."""
        step = recipe.steps["begin_archival"]
        assert step.on_result is not None
        conditions = step.on_result.conditions
        truthy_route = next((c for c in conditions if c.when and "pr_url" in c.when), None)
        assert truthy_route is not None
        assert truthy_route.route == "capture_experiment_branch"

    def test_archival_begin_archival_default_to_complete(self, recipe) -> None:
        """begin_archival default route goes to research_complete."""
        step = recipe.steps["begin_archival"]
        conditions = step.on_result.conditions
        default = next((c for c in conditions if not c.when), None)
        assert default is not None
        assert default.route == "research_complete"

    def test_archival_capture_experiment_branch_step(self, recipe) -> None:
        """capture_experiment_branch captures experiment_branch from git rev-parse."""
        assert "capture_experiment_branch" in recipe.steps
        step = recipe.steps["capture_experiment_branch"]
        assert step.tool == "run_cmd"
        assert "experiment_branch" in step.capture
        assert step.on_success == "create_artifact_branch"
        assert step.on_failure == "research_complete"

    def test_archival_create_artifact_branch_step(self, recipe) -> None:
        """create_artifact_branch creates temp worktree and captures artifact_branch."""
        assert "create_artifact_branch" in recipe.steps
        step = recipe.steps["create_artifact_branch"]
        assert step.tool == "run_cmd"
        assert "artifact_branch" in step.capture
        assert step.on_success == "open_artifact_pr"
        assert step.on_failure == "research_complete"

    def test_archival_create_artifact_branch_uses_research_checkout(self, recipe) -> None:
        """create_artifact_branch must use git checkout <branch> -- research/ pattern."""
        step = recipe.steps["create_artifact_branch"]
        cmd = step.with_args["cmd"]
        assert "research/" in cmd, "Must checkout research/ directory from experiment branch"
        assert "worktree add" in cmd, "Must create a temporary worktree"

    def test_archival_open_artifact_pr_step(self, recipe) -> None:
        """open_artifact_pr creates the artifact-only PR and captures artifact_pr_url."""
        assert "open_artifact_pr" in recipe.steps
        step = recipe.steps["open_artifact_pr"]
        assert step.tool == "run_cmd"
        assert "artifact_pr_url" in step.capture
        assert step.on_success == "tag_experiment_branch"
        assert step.on_failure == "research_complete"

    def test_archival_tag_experiment_branch_step(self, recipe) -> None:
        """tag_experiment_branch creates annotated archive tag and captures archive_tag."""
        assert "tag_experiment_branch" in recipe.steps
        step = recipe.steps["tag_experiment_branch"]
        assert step.tool == "run_cmd"
        assert "archive_tag" in step.capture
        assert step.on_success == "close_experiment_pr"
        assert step.on_failure == "research_complete"

    def test_archival_tag_uses_archive_prefix(self, recipe) -> None:
        """Tag name must use archive/research/ prefix convention."""
        step = recipe.steps["tag_experiment_branch"]
        cmd = step.with_args["cmd"]
        assert "archive/research/" in cmd

    def test_archival_close_experiment_pr_step(self, recipe) -> None:
        """close_experiment_pr closes the original PR and routes to research_complete."""
        assert "close_experiment_pr" in recipe.steps
        step = recipe.steps["close_experiment_pr"]
        assert step.tool == "run_cmd"
        assert step.on_success == "research_complete"
        assert step.on_failure == "research_complete"

    def test_archival_close_pr_references_artifact_and_tag(self, recipe) -> None:
        """close_experiment_pr comment must reference both artifact PR and archive tag."""
        step = recipe.steps["close_experiment_pr"]
        cmd = step.with_args["cmd"]
        assert "artifact_pr_url" in cmd, "Must reference artifact PR URL"
        assert "archive_tag" in cmd, "Must reference archive tag"

    def test_archival_graceful_degradation(self, recipe) -> None:
        """Every archival step must route on_failure to research_complete."""
        archival_steps = [
            "capture_experiment_branch",
            "create_artifact_branch",
            "open_artifact_pr",
            "tag_experiment_branch",
            "close_experiment_pr",
        ]
        for name in archival_steps:
            step = recipe.steps[name]
            assert step.on_failure == "research_complete", (
                f"{name}.on_failure must be research_complete for graceful degradation"
            )

    def test_re_push_research_routes_to_finalize_bundle_render(self, recipe) -> None:
        """re_push_research routes to finalize_bundle_render on success."""
        step = recipe.steps["re_push_research"]
        assert step.on_success == "finalize_bundle_render"
        assert step.on_failure == "begin_archival"

    def test_finalize_bundle_routes_to_re_push_research(self, recipe) -> None:
        """finalize_bundle on_success routes to re_push_research (push includes the commit)."""
        step = recipe.steps["finalize_bundle"]
        assert step.on_success == "re_push_research"
        assert step.on_failure == "begin_archival"

    def test_finalize_bundle_render_step_exists_and_routes(self, recipe) -> None:
        """finalize_bundle_render routes to route_archive_or_export on both outcomes."""
        assert "finalize_bundle_render" in recipe.steps
        step = recipe.steps["finalize_bundle_render"]
        assert step.on_success == "route_archive_or_export"
        assert step.on_failure == "route_archive_or_export"

    def test_create_worktree_copies_review_cycle_artifacts(self, recipe) -> None:
        """create_worktree must copy review-design dashboards and revision guidance."""
        step = recipe.steps["create_worktree"]
        cmd = step.with_args["cmd"]
        assert "review-cycles" in cmd, (
            "create_worktree must create artifacts/review-cycles/ subdirectory"
        )
        assert "evaluation_dashboard" in cmd and "review-cycles" in cmd, (
            "create_worktree must copy evaluation dashboards to review-cycles/"
        )
        assert "revision_guidance" in cmd and "review-cycles" in cmd, (
            "create_worktree must copy revision guidance to review-cycles/"
        )

    def test_create_worktree_copies_plan_version_artifacts(self, recipe) -> None:
        """create_worktree must copy intermediate plan versions."""
        step = recipe.steps["create_worktree"]
        cmd = step.with_args["cmd"]
        assert "plan-versions" in cmd, (
            "create_worktree must create artifacts/plan-versions/ subdirectory"
        )
        assert "experiment_plan" in cmd and "plan-versions" in cmd, (
            "create_worktree must copy plan versions to plan-versions/"
        )

    def test_stage_bundle_step_exists(self, recipe) -> None:
        """A stage_bundle step must exist to organize phase artifacts."""
        assert "stage_bundle" in recipe.steps, (
            "research.yaml must have a stage_bundle step for phase artifact staging"
        )
        step = recipe.steps["stage_bundle"]
        cmd = step.with_args["cmd"]
        assert "phase-groups" in cmd, "Must copy make-groups output"
        assert "phase-plans" in cmd, "Must copy make-plan output"

    def test_test_routes_to_push_branch(self, recipe) -> None:
        """test step must route to push_branch now that commit_research_artifacts is removed."""
        step = recipe.steps["test"]
        assert step.on_success == "push_branch", (
            "test.on_success must be push_branch after commit_research_artifacts is removed"
        )

    def test_retest_routes_to_push_branch(self, recipe) -> None:
        """retest step must route to push_branch now that commit_research_artifacts is removed."""
        step = recipe.steps["retest"]
        assert step.on_success == "push_branch", (
            "retest.on_success must be push_branch after commit_research_artifacts is removed"
        )

    def test_stage_bundle_routes_to_route_pr_or_local(self, recipe) -> None:
        """stage_bundle must route to route_pr_or_local on success and failure."""
        step = recipe.steps["stage_bundle"]
        assert step.on_success == "route_pr_or_local"
        assert step.on_failure == "route_pr_or_local"
