import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe_structure

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_research_recipe_validates_after_diag_changes(recipe):
    """research.yaml must pass structural validation with new troubleshoot steps."""
    errors = validate_recipe_structure(recipe)
    assert not errors, f"Validation errors: {errors}"


def test_research_recipe_has_troubleshoot_step(recipe):
    """research.yaml must contain the troubleshoot_implement_failure step."""
    step_names = list(recipe.steps.keys())
    assert "troubleshoot_implement_failure" in step_names
    assert "route_implement_failure" in step_names


def test_implement_phase_failure_routes_to_troubleshoot(recipe):
    """implement_phase on_failure must route to troubleshoot_implement_failure."""
    step = recipe.steps["implement_phase"]
    assert step.on_failure == "troubleshoot_implement_failure"


def test_implement_phase_exhausted_routes_to_audit_impl(recipe):
    """implement_phase on_exhausted must route to audit_impl."""
    step = recipe.steps["implement_phase"]
    assert step.on_exhausted == "audit_impl"


def test_implement_phase_uses_implement_experiment(recipe):
    """implement_phase must use implement-experiment, not retry-worktree."""
    step = recipe.steps["implement_phase"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "implement-experiment" in skill_cmd
    assert "retry-worktree" not in skill_cmd


def test_troubleshoot_step_captures_required_tokens(recipe):
    """troubleshoot_implement_failure must capture is_fixable for downstream routing."""
    step = recipe.steps["troubleshoot_implement_failure"]
    capture = step.capture or {}
    assert "is_fixable" in capture


def test_research_recipe_has_three_pr_prep_steps(recipe):
    """open_research_pr is replaced by three decomposed steps."""
    step_names = list(recipe.steps.keys())
    assert "open_research_pr" not in step_names
    assert "prepare_research_pr" in step_names
    assert "run_experiment_lenses" in step_names
    assert "compose_research_pr" in step_names


def test_push_branch_routes_to_prepare_research_pr(recipe):
    """push_branch.on_success must route to prepare_research_pr."""
    step = recipe.steps["push_branch"]
    assert step.on_success == "prepare_research_pr"


def test_prepare_research_pr_routes_to_run_experiment_lenses(recipe):
    """prepare_research_pr.on_success must route to run_experiment_lenses."""
    step = recipe.steps["prepare_research_pr"]
    assert step.on_success == "run_experiment_lenses"


def test_run_experiment_lenses_routes_to_stage_bundle_on_success(recipe):
    """run_experiment_lenses.on_success routes to stage_bundle."""
    step = recipe.steps["run_experiment_lenses"]
    assert step.on_success == "stage_bundle"


def test_run_experiment_lenses_routes_to_stage_bundle_on_failure(recipe):
    """run_experiment_lenses.on_failure routes to stage_bundle (partial diagrams OK)."""
    step = recipe.steps["run_experiment_lenses"]
    assert step.on_failure == "stage_bundle"


def test_compose_research_pr_routes_to_guard_pr_url(recipe):
    """compose_research_pr.on_success routes to guard_pr_url."""
    step = recipe.steps["compose_research_pr"]
    assert step.on_success == "guard_pr_url"


def test_prepare_research_pr_captures_prep_path(recipe):
    """prepare_research_pr must capture prep_path for compose step."""
    step = recipe.steps["prepare_research_pr"]
    assert "prep_path" in (step.capture or {})


def test_prepare_research_pr_uses_context_experiment_plan(recipe):
    """prepare_research_pr must pass ${{ context.experiment_plan }}, not a hardcoded path."""
    step = recipe.steps["prepare_research_pr"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "context.experiment_plan" in skill_cmd
    assert ".autoskillit/temp/experiment-plan.md" not in skill_cmd


def test_run_experiment_lenses_has_capture_list_for_diagram_paths(recipe):
    """run_experiment_lenses accumulates diagram paths via capture_list."""
    step = recipe.steps["run_experiment_lenses"]
    assert "all_diagram_paths" in (step.capture_list or {})


def test_stage_bundle_exists(recipe):
    """stage_bundle step must exist in the recipe."""
    assert "stage_bundle" in recipe.steps, "research.yaml must have a stage_bundle step"


def test_run_experiment_failure_routes_to_troubleshoot(recipe):
    """run_experiment on_failure must route to troubleshoot_run_failure."""
    step = recipe.steps["run_experiment"]
    assert step.on_failure == "troubleshoot_run_failure"


def test_research_recipe_has_troubleshoot_run_steps(recipe):
    """research.yaml must contain the troubleshoot_run_failure and route_run_failure steps."""
    step_names = list(recipe.steps.keys())
    assert "troubleshoot_run_failure" in step_names
    assert "route_run_failure" in step_names


def test_troubleshoot_run_captures_required_tokens(recipe):
    """troubleshoot_run_failure must capture run_is_fixable for downstream routing."""
    step = recipe.steps["troubleshoot_run_failure"]
    capture = step.capture or {}
    assert "run_is_fixable" in capture


def test_troubleshoot_run_uses_run_experiment_step_name(recipe):
    """troubleshoot_run_failure skill_command must pass run_experiment, not implement_phase."""
    step = recipe.steps["troubleshoot_run_failure"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "troubleshoot-experiment" in skill_cmd
    assert "run_experiment" in skill_cmd
    assert "implement_phase" not in skill_cmd


def test_route_run_failure_routes_fixable_to_adjust(recipe):
    """route_run_failure must route fixable failures to adjust_experiment."""
    step = recipe.steps["route_run_failure"]
    assert step.action == "route"
    conditions = step.on_result.conditions
    fixable_cond = next(c for c in conditions if c.when and "run_is_fixable" in c.when)
    assert fixable_cond.route == "adjust_experiment"


def test_route_run_failure_default_escalates(recipe):
    """route_run_failure catch-all must route to escalate_stop."""
    step = recipe.steps["route_run_failure"]
    conditions = step.on_result.conditions
    default_cond = next(c for c in conditions if c.when is None)
    assert default_cond.route == "escalate_stop"


def test_adjust_experiment_routing_unchanged(recipe):
    """adjust_experiment uses on_result for all verdict values (not on_success)."""
    step = recipe.steps["adjust_experiment"]
    assert step.on_result is not None, "adjust_experiment must use on_result for verdict routing"
    assert step.on_success is None, "adjust_experiment must not use on_success"
    conditions = step.on_result.conditions
    conclusive = next((c for c in conditions if c.when and "CONCLUSIVE" in c.when), None)
    assert conclusive is not None and conclusive.route == "check_run_fix_loop"
    blocked = next((c for c in conditions if c.when and "BLOCKED" in c.when), None)
    assert blocked is not None and blocked.route == "escalate_stop"
    inconclusive = next((c for c in conditions if c.when and "INCONCLUSIVE" in c.when), None)
    assert inconclusive is not None and inconclusive.route == "check_run_fix_loop"
    assert step.on_failure == "ensure_results"


def test_run_experiment_exhausted_unchanged(recipe):
    """run_experiment on_exhausted must still route to ensure_results."""
    step = recipe.steps["run_experiment"]
    assert step.on_exhausted == "ensure_results"


def test_troubleshoot_step_captures_retry_delay(recipe):
    """troubleshoot_implement_failure must capture retry_delay for downstream delay gate."""
    step = recipe.steps["troubleshoot_implement_failure"]
    capture = step.capture or {}
    assert "retry_delay" in capture


def test_troubleshoot_run_failure_captures_run_retry_delay(recipe):
    """troubleshoot_run_failure must capture run_retry_delay for downstream delay gate."""
    step = recipe.steps["troubleshoot_run_failure"]
    capture = step.capture or {}
    assert "run_retry_delay" in capture


def test_research_recipe_has_implement_retry_delay_step(recipe):
    """research.yaml must contain the implement_retry_delay run_cmd step."""
    assert "implement_retry_delay" in recipe.steps
    step = recipe.steps["implement_retry_delay"]
    assert step.tool == "run_cmd"
    assert "sleep" in step.with_args.get("cmd", "")


def test_research_recipe_has_route_implement_retry_delay(recipe):
    """research.yaml must contain the route_implement_retry_delay routing step."""
    assert "route_implement_retry_delay" in recipe.steps
    step = recipe.steps["route_implement_retry_delay"]
    assert step.action == "route"


def test_research_recipe_has_run_retry_delay_step(recipe):
    """research.yaml must contain the run_retry_delay run_cmd step."""
    assert "run_retry_delay" in recipe.steps
    step = recipe.steps["run_retry_delay"]
    assert step.tool == "run_cmd"
    assert "sleep" in step.with_args.get("cmd", "")


def test_research_recipe_has_route_run_retry_delay(recipe):
    """research.yaml must contain the route_run_retry_delay routing step."""
    assert "route_run_retry_delay" in recipe.steps
    step = recipe.steps["route_run_retry_delay"]
    assert step.action == "route"


def test_check_implement_fix_loop_routes_to_delay_gate(recipe):
    """Loop guard must route through delay gate, not directly to plan_phase."""
    step = recipe.steps["check_implement_fix_loop"]
    assert step.on_result is not None, "check_implement_fix_loop must have on_result"
    conditions = step.on_result.conditions
    assert conditions, "check_implement_fix_loop on_result must have non-empty conditions"
    non_exhausted = [c for c in conditions if c.when is None or "max_exceeded" not in c.when]
    assert any(c.route == "route_implement_retry_delay" for c in non_exhausted)


def test_check_run_fix_loop_routes_to_delay_gate(recipe):
    """Run loop guard must route through delay gate, not directly to run_experiment."""
    step = recipe.steps["check_run_fix_loop"]
    assert step.on_result is not None, "check_run_fix_loop must have on_result"
    conditions = step.on_result.conditions
    assert conditions, "check_run_fix_loop on_result must have non-empty conditions"
    non_exhausted = [c for c in conditions if c.when is None or "max_exceeded" not in c.when]
    assert any(c.route == "route_run_retry_delay" for c in non_exhausted)


# ---------------------------------------------------------------------------
# run_experiment verdict routing tests
# ---------------------------------------------------------------------------


def test_run_experiment_uses_verdict_routing(recipe):
    """run_experiment must use on_result for verdict-based routing, not on_success."""
    step = recipe.steps["run_experiment"]
    assert step.on_result is not None, (
        "run_experiment must have on_result block for verdict-based routing"
    )
    assert step.on_success is None, (
        "run_experiment must not use on_success (replaced by on_result)"
    )


def test_run_experiment_conclusive_routes_to_generate_report(recipe):
    """CONCLUSIVE verdict must route to generate_report."""
    step = recipe.steps["run_experiment"]
    conditions = step.on_result.conditions
    conclusive = next(
        (c for c in conditions if c.when and "CONCLUSIVE" in c.when),
        None,
    )
    assert conclusive is not None, (
        "run_experiment on_result must have a CONCLUSIVE verdict condition"
    )
    assert conclusive.route == "generate_report", (
        f"CONCLUSIVE must route to generate_report, got {conclusive.route}"
    )


def test_run_experiment_blocked_routes_to_escalate_stop(recipe):
    """BLOCKED verdict must route to escalate_stop."""
    step = recipe.steps["run_experiment"]
    conditions = step.on_result.conditions
    blocked = next(
        (c for c in conditions if c.when and "BLOCKED" in c.when),
        None,
    )
    assert blocked is not None, "run_experiment on_result must have a BLOCKED verdict condition"
    assert blocked.route == "escalate_stop", (
        f"BLOCKED must route to escalate_stop, got {blocked.route}"
    )


def test_run_experiment_inconclusive_routes_to_ensure_results(recipe):
    """INCONCLUSIVE verdict must route to ensure_results."""
    step = recipe.steps["run_experiment"]
    conditions = step.on_result.conditions
    inconclusive = next(
        (c for c in conditions if c.when and "INCONCLUSIVE" in c.when),
        None,
    )
    assert inconclusive is not None, (
        "run_experiment on_result must have an INCONCLUSIVE verdict condition"
    )
    assert inconclusive.route == "ensure_results", (
        f"INCONCLUSIVE must route to ensure_results, got {inconclusive.route}"
    )


# ---------------------------------------------------------------------------
# re_run_experiment verdict routing tests (research.yaml)
# ---------------------------------------------------------------------------


def test_re_run_experiment_uses_verdict_routing(recipe):
    """re_run_experiment must use on_result for verdict-based routing, not on_success."""
    step = recipe.steps["re_run_experiment"]
    assert step.on_result is not None, (
        "re_run_experiment must have on_result block for verdict-based routing"
    )
    assert step.on_success is None, (
        "re_run_experiment must not use on_success (replaced by on_result)"
    )


def test_re_run_experiment_conclusive_routes_to_re_generate_report(recipe):
    """CONCLUSIVE verdict must route to re_generate_report."""
    step = recipe.steps["re_run_experiment"]
    conditions = step.on_result.conditions
    conclusive = next(
        (c for c in conditions if c.when and "CONCLUSIVE" in c.when),
        None,
    )
    assert conclusive is not None, (
        "re_run_experiment on_result must have a CONCLUSIVE verdict condition"
    )
    assert conclusive.route == "re_generate_report", (
        f"CONCLUSIVE must route to re_generate_report, got {conclusive.route}"
    )


def test_re_run_experiment_blocked_routes_to_escalate_stop(recipe):
    """BLOCKED verdict must route to escalate_stop."""
    step = recipe.steps["re_run_experiment"]
    conditions = step.on_result.conditions
    blocked = next(
        (c for c in conditions if c.when and "BLOCKED" in c.when),
        None,
    )
    assert blocked is not None, "re_run_experiment on_result must have a BLOCKED verdict condition"
    assert blocked.route == "escalate_stop", (
        f"BLOCKED must route to escalate_stop, got {blocked.route}"
    )


def test_re_run_experiment_inconclusive_routes_to_re_push_research(recipe):
    """INCONCLUSIVE verdict must route to re_push_research."""
    step = recipe.steps["re_run_experiment"]
    conditions = step.on_result.conditions
    inconclusive = next(
        (c for c in conditions if c.when and "INCONCLUSIVE" in c.when),
        None,
    )
    assert inconclusive is not None, (
        "re_run_experiment on_result must have an INCONCLUSIVE verdict condition"
    )
    assert inconclusive.route == "re_push_research", (
        f"INCONCLUSIVE must route to re_push_research, got {inconclusive.route}"
    )


# ---------------------------------------------------------------------------
# research-review.yaml re_run_experiment verdict routing tests
# ---------------------------------------------------------------------------


REVIEW_RECIPE_PATH = builtin_recipes_dir() / "research-review.yaml"


@pytest.fixture(scope="module")
def review_recipe():
    return load_recipe(REVIEW_RECIPE_PATH)


def test_review_re_run_experiment_uses_verdict_routing(review_recipe):
    """research-review re_run_experiment must use on_result, not on_success."""
    step = review_recipe.steps["re_run_experiment"]
    assert step.on_result is not None, (
        "re_run_experiment in research-review must have on_result block"
    )
    assert step.on_success is None, "re_run_experiment in research-review must not use on_success"


def test_review_re_run_experiment_conclusive_routes_to_re_generate_report(review_recipe):
    """CONCLUSIVE verdict must route to re_generate_report in research-review."""
    step = review_recipe.steps["re_run_experiment"]
    conditions = step.on_result.conditions
    conclusive = next(
        (c for c in conditions if c.when and "CONCLUSIVE" in c.when),
        None,
    )
    assert conclusive is not None, (
        "re_run_experiment in research-review must have a CONCLUSIVE verdict condition"
    )
    assert conclusive.route == "re_generate_report", (
        f"CONCLUSIVE must route to re_generate_report, got {conclusive.route}"
    )


def test_review_re_run_experiment_blocked_routes_to_escalate_stop(review_recipe):
    """BLOCKED verdict must route to escalate_stop in research-review."""
    step = review_recipe.steps["re_run_experiment"]
    conditions = step.on_result.conditions
    blocked = next(
        (c for c in conditions if c.when and "BLOCKED" in c.when),
        None,
    )
    assert blocked is not None, (
        "re_run_experiment in research-review must have a BLOCKED verdict condition"
    )
    assert blocked.route == "escalate_stop", (
        f"BLOCKED must route to escalate_stop, got {blocked.route}"
    )


def test_review_re_run_experiment_inconclusive_routes_to_re_push_research(review_recipe):
    """INCONCLUSIVE verdict must route to re_push_research in research-review."""
    step = review_recipe.steps["re_run_experiment"]
    conditions = step.on_result.conditions
    inconclusive = next(
        (c for c in conditions if c.when and "INCONCLUSIVE" in c.when),
        None,
    )
    assert inconclusive is not None, (
        "re_run_experiment in research-review must have an INCONCLUSIVE verdict condition"
    )
    assert inconclusive.route == "re_push_research", (
        f"INCONCLUSIVE must route to re_push_research, got {inconclusive.route}"
    )
