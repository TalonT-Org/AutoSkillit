"""Queue mode structural assertions shared across all queue-capable recipes
(parametrized over any_recipe or QUEUE_RECIPES)."""

from __future__ import annotations

import re

import pytest

from autoskillit.core import PRState
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Local fixture (depends on conftest-provided fixtures)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=["impl", "remed", "impl_groups"])
def any_recipe(request, impl_recipe, remed_recipe, impl_groups_recipe):
    return {"impl": impl_recipe, "remed": remed_recipe, "impl_groups": impl_groups_recipe}[
        request.param
    ]


# ---------------------------------------------------------------------------
# Queue recipe auto-discovery — any recipe with wait_for_merge_queue routing
# ---------------------------------------------------------------------------


def _discover_queue_recipe_fixtures() -> list[str]:
    """Return fixture names for all bundled recipes with wait_for_merge_queue routing."""
    fixture_map = {
        "implementation": "impl_recipe",
        "remediation": "remed_recipe",
        "implementation-groups": "impl_groups_recipe",
        "merge-prs": "pmp_recipe",
    }
    result = []
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        name = yaml_path.stem
        recipe = load_recipe(yaml_path)
        for step in recipe.steps.values():
            if (
                step.tool == "wait_for_merge_queue"
                and step.on_result is not None
                and getattr(step.on_result, "conditions", None)
            ):
                fixture_name = fixture_map.get(name)
                if fixture_name:
                    result.append(fixture_name)
                break
    return sorted(result)


# Intentional: fail-fast at collection time if a bundled recipe YAML is malformed.
QUEUE_RECIPES = _discover_queue_recipe_fixtures()


# ---------------------------------------------------------------------------
# any_recipe parametrized — auto_merge ingredient
# ---------------------------------------------------------------------------


def test_auto_merge_ingredient(any_recipe) -> None:
    assert "auto_merge" in any_recipe.ingredients
    ing = any_recipe.ingredients["auto_merge"]
    assert ing.default == "true"
    assert ing.required is False


def test_route_queue_mode_auto_merge_condition_first(any_recipe) -> None:
    step = any_recipe.steps["route_queue_mode"]
    conds = step.on_result.conditions
    auto_merge_idx = next(i for i, c in enumerate(conds) if c.when and "auto_merge" in c.when)
    queue_available_idx = next(
        i for i, c in enumerate(conds) if c.when and "queue_available" in c.when
    )
    assert auto_merge_idx < queue_available_idx


def test_auto_merge_false_routes_to_register_clone_success(any_recipe) -> None:
    step = any_recipe.steps["route_queue_mode"]
    auto_merge_cond = next(
        c for c in step.on_result.conditions if c.when and "auto_merge" in c.when
    )
    assert auto_merge_cond.when == "${{ inputs.auto_merge }} != 'true'"
    assert auto_merge_cond.route == "patch_token_summary"


# ---------------------------------------------------------------------------
# Direct merge fallback — all three affected recipes
# ---------------------------------------------------------------------------


def test_route_queue_mode_default_routes_to_immediate_merge(any_recipe) -> None:
    """Default (fallthrough) condition must route to immediate_merge, not direct_merge."""
    step = any_recipe.steps["route_queue_mode"]
    default_cond = next((c for c in step.on_result.conditions if c.when is None), None)
    assert default_cond is not None, "Expected a default (when=None) condition in route_queue_mode"
    assert default_cond.route == "immediate_merge"


def test_direct_merge_step_exists(any_recipe) -> None:
    assert "direct_merge" in any_recipe.steps
    step = any_recipe.steps["direct_merge"]
    assert step.tool == "run_cmd"


def test_direct_merge_routes_to_wait_for_direct_merge(any_recipe) -> None:
    step = any_recipe.steps["direct_merge"]
    assert step.on_success == "wait_for_direct_merge"


def test_direct_merge_failure_routes_to_release_issue_failure(any_recipe) -> None:
    step = any_recipe.steps["direct_merge"]
    assert step.on_failure == "release_issue_failure"


def test_wait_for_direct_merge_step_exists(any_recipe) -> None:
    assert "wait_for_direct_merge" in any_recipe.steps
    step = any_recipe.steps["wait_for_direct_merge"]
    assert step.tool == "run_python"


def test_wait_for_direct_merge_merged_routes_to_success(any_recipe) -> None:
    step = any_recipe.steps["wait_for_direct_merge"]
    merged_cond = next(
        (c for c in step.on_result.conditions if c.when and "merged" in c.when), None
    )
    assert merged_cond is not None, "Expected a 'merged' condition in wait_for_direct_merge"
    assert merged_cond.route == "release_issue_success"


def test_wait_for_direct_merge_closed_routes_to_conflict_fix(any_recipe) -> None:
    step = any_recipe.steps["wait_for_direct_merge"]
    closed_cond = next(
        (c for c in step.on_result.conditions if c.when and "closed" in c.when), None
    )
    assert closed_cond is not None, "Expected a 'closed' condition in wait_for_direct_merge"
    assert closed_cond.route == "direct_merge_conflict_fix"


def test_direct_merge_conflict_fix_exists(any_recipe) -> None:
    assert "direct_merge_conflict_fix" in any_recipe.steps
    step = any_recipe.steps["direct_merge_conflict_fix"]
    assert step.tool == "run_python"


def test_direct_merge_conflict_fix_clean_routes_to_re_push(any_recipe) -> None:
    step = any_recipe.steps["direct_merge_conflict_fix"]
    clean_route = next(
        (c.route for c in step.on_result.conditions if c.when and "clean" in c.when),
        None,
    )
    assert clean_route == "re_push_direct_fix"


def test_resolve_direct_merge_conflicts_exists_with_run_skill(any_recipe) -> None:
    assert "resolve_direct_merge_conflicts" in any_recipe.steps
    assert any_recipe.steps["resolve_direct_merge_conflicts"].tool == "run_skill"


def test_re_push_direct_fix_exists(any_recipe) -> None:
    assert "re_push_direct_fix" in any_recipe.steps
    step = any_recipe.steps["re_push_direct_fix"]
    assert step.tool == "push_to_remote"
    assert step.on_success == "redirect_merge"


def test_redirect_merge_step_exists(any_recipe) -> None:
    assert "redirect_merge" in any_recipe.steps
    step = any_recipe.steps["redirect_merge"]
    assert step.tool == "run_cmd"
    assert step.on_success == "wait_for_direct_merge"


def test_direct_merge_steps_have_skip_when_false(any_recipe) -> None:
    new_steps = [
        "direct_merge",
        "wait_for_direct_merge",
        "direct_merge_conflict_fix",
        "resolve_direct_merge_conflicts",
        "re_push_direct_fix",
        "redirect_merge",
    ]
    for step_name in new_steps:
        assert step_name in any_recipe.steps, f"Missing step: {step_name}"
        step = any_recipe.steps[step_name]
        assert step.skip_when_false == "inputs.open_pr", (
            f"{step_name}.skip_when_false must be 'inputs.open_pr'"
        )


# ---------------------------------------------------------------------------
# check_repo_merge_state — consolidated pre-queue gate step (any_recipe)
# ---------------------------------------------------------------------------


def test_check_repo_merge_state_step_exists(any_recipe) -> None:
    """check_repo_merge_state step must exist in all three queue-capable recipes."""
    assert "check_repo_merge_state" in any_recipe.steps


def test_check_repo_merge_state_captures_all_three_fields(any_recipe) -> None:
    step = any_recipe.steps["check_repo_merge_state"]
    assert set(step.capture or {}) >= {
        "queue_available",
        "merge_group_trigger",
        "auto_merge_available",
    }


def test_check_repo_merge_state_routes_to_route_queue_mode_on_success(any_recipe) -> None:
    step = any_recipe.steps["check_repo_merge_state"]
    assert step.on_success == "route_queue_mode"


def test_check_repo_merge_state_has_skip_when_false(any_recipe) -> None:
    step = any_recipe.steps["check_repo_merge_state"]
    assert step.skip_when_false == "inputs.open_pr"


def test_check_repo_merge_state_is_in_pre_queue_gate_block(any_recipe) -> None:
    step = any_recipe.steps["check_repo_merge_state"]
    assert step.block == "pre_queue_gate"


def test_route_queue_mode_has_auto_merge_available_condition(any_recipe) -> None:
    """route_queue_mode must have an explicit condition for auto_merge_available == true."""
    step = any_recipe.steps["route_queue_mode"]
    conds = step.on_result.conditions
    assert any(c.when and "auto_merge_available" in c.when for c in conds)


def test_route_queue_mode_auto_merge_available_routes_to_direct_merge(any_recipe) -> None:
    step = any_recipe.steps["route_queue_mode"]
    cond = next(
        c
        for c in step.on_result.conditions
        if c.when and "auto_merge_available" in c.when and "queue_available" not in c.when
    )
    assert cond.when == "${{ context.auto_merge_available }} == true"
    assert cond.route == "direct_merge"


# ---------------------------------------------------------------------------
# Immediate merge path — new for autoMergeAllowed=false repos
# ---------------------------------------------------------------------------


def test_immediate_merge_step_exists(any_recipe) -> None:
    assert "immediate_merge" in any_recipe.steps
    step = any_recipe.steps["immediate_merge"]
    assert step.tool == "run_cmd"


def test_immediate_merge_uses_squash_without_auto(any_recipe) -> None:
    """immediate_merge must use --squash without --auto."""
    step = any_recipe.steps["immediate_merge"]
    cmd = step.with_args.get("cmd", "")
    assert "--squash" in cmd
    assert "--auto" not in cmd


def test_immediate_merge_routes_to_wait_for_immediate_merge(any_recipe) -> None:
    step = any_recipe.steps["immediate_merge"]
    assert step.on_success == "wait_for_immediate_merge"


def test_immediate_merge_failure_routes_to_release_issue_failure(any_recipe) -> None:
    step = any_recipe.steps["immediate_merge"]
    assert step.on_failure == "release_issue_failure"


def test_wait_for_immediate_merge_step_exists(any_recipe) -> None:
    assert "wait_for_immediate_merge" in any_recipe.steps
    step = any_recipe.steps["wait_for_immediate_merge"]
    assert step.tool == "run_python"


def test_wait_for_immediate_merge_merged_routes_to_success(any_recipe) -> None:
    step = any_recipe.steps["wait_for_immediate_merge"]
    merged_cond = next(
        (c for c in step.on_result.conditions if c.when and "merged" in c.when),
        None,
    )
    assert merged_cond is not None
    assert merged_cond.route == "release_issue_success"


def test_wait_for_immediate_merge_closed_routes_to_conflict_fix(any_recipe) -> None:
    step = any_recipe.steps["wait_for_immediate_merge"]
    closed_cond = next(
        (c for c in step.on_result.conditions if c.when and "closed" in c.when),
        None,
    )
    assert closed_cond is not None
    assert closed_cond.route == "immediate_merge_conflict_fix"


def test_immediate_merge_conflict_fix_exists(any_recipe) -> None:
    assert "immediate_merge_conflict_fix" in any_recipe.steps
    step = any_recipe.steps["immediate_merge_conflict_fix"]
    assert step.tool == "run_python"


def test_immediate_merge_conflict_fix_clean_routes_to_re_push(any_recipe) -> None:
    step = any_recipe.steps["immediate_merge_conflict_fix"]
    clean_route = next(
        (c.route for c in step.on_result.conditions if c.when and "clean" in c.when),
        None,
    )
    assert clean_route == "re_push_immediate_fix"


def test_resolve_immediate_merge_conflicts_exists_with_run_skill(any_recipe) -> None:
    assert "resolve_immediate_merge_conflicts" in any_recipe.steps
    assert any_recipe.steps["resolve_immediate_merge_conflicts"].tool == "run_skill"


def test_re_push_immediate_fix_exists(any_recipe) -> None:
    assert "re_push_immediate_fix" in any_recipe.steps
    step = any_recipe.steps["re_push_immediate_fix"]
    assert step.tool == "push_to_remote"
    assert step.on_success == "remerge_immediate"


def test_remerge_immediate_exists(any_recipe) -> None:
    assert "remerge_immediate" in any_recipe.steps
    step = any_recipe.steps["remerge_immediate"]
    assert step.tool == "run_cmd"
    assert step.on_success == "wait_for_immediate_merge"


def test_remerge_immediate_uses_squash_without_auto(any_recipe) -> None:
    """remerge_immediate must also use --squash without --auto."""
    step = any_recipe.steps["remerge_immediate"]
    cmd = step.with_args.get("cmd", "")
    assert "--squash" in cmd
    assert "--auto" not in cmd


def test_all_immediate_merge_steps_have_skip_when_false(any_recipe) -> None:
    immediate_steps = [
        "immediate_merge",
        "wait_for_immediate_merge",
        "immediate_merge_conflict_fix",
        "resolve_immediate_merge_conflicts",
        "re_push_immediate_fix",
        "remerge_immediate",
    ]
    for step_name in immediate_steps:
        assert step_name in any_recipe.steps, f"Missing step: {step_name}"
        step = any_recipe.steps[step_name]
        assert step.skip_when_false == "inputs.open_pr", (
            f"{step_name}.skip_when_false must be 'inputs.open_pr'"
        )


def test_auto_merge_ingredient_description_updated(any_recipe) -> None:
    ing = any_recipe.ingredients["auto_merge"]
    assert "direct merge" in ing.description.lower() or "direct" in ing.description.lower(), (
        "auto_merge description must mention direct merge as an alternative to queue"
    )


def test_implementation_recipe_still_valid(impl_recipe) -> None:
    errors = validate_recipe(impl_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


def test_remediation_recipe_still_valid(remed_recipe) -> None:
    errors = validate_recipe(remed_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


def test_impl_groups_recipe_still_valid(impl_groups_recipe) -> None:
    errors = validate_recipe(impl_groups_recipe)
    assert errors == [], f"validate_recipe errors: {errors}"


# ---------------------------------------------------------------------------
# Routing matrix exhaustiveness — queue_available × auto_merge_available
# ---------------------------------------------------------------------------


def test_route_queue_mode_queue_routes_to_enqueue_to_queue(any_recipe) -> None:
    """queue+merge_group_trigger cell must route to enqueue_to_queue (unified)."""
    step = any_recipe.steps["route_queue_mode"]
    cond = next(
        c
        for c in step.on_result.conditions
        if c.when and "queue_available" in c.when and "merge_group_trigger" in c.when
    )
    assert cond.route == "enqueue_to_queue"
    # The unified route must NOT split on auto_merge_available
    assert "auto_merge_available" not in (cond.when or "")


def test_route_queue_mode_no_queue_with_auto_routes_to_direct_merge(any_recipe) -> None:
    """no-queue+auto cell must route to direct_merge."""
    step = any_recipe.steps["route_queue_mode"]
    cond = next(
        c
        for c in step.on_result.conditions
        if c.when and "auto_merge_available" in c.when and "queue_available" not in c.when
    )
    assert cond.route == "direct_merge"


def test_route_queue_mode_no_queue_no_auto_falls_through_to_immediate_merge(
    any_recipe,
) -> None:
    """Default (when is None) condition must route to immediate_merge."""
    step = any_recipe.steps["route_queue_mode"]
    cond = next(c for c in step.on_result.conditions if c.when is None)
    assert cond.route == "immediate_merge"


def test_enqueue_to_queue_route_count(any_recipe) -> None:
    """Exactly one condition must route to enqueue_to_queue."""
    step = any_recipe.steps["route_queue_mode"]
    count = sum(1 for c in step.on_result.conditions if c.route == "enqueue_to_queue")
    assert count == 1, f"Expected exactly 1 enqueue_to_queue route, got {count}"


# ---------------------------------------------------------------------------
# Unified enrollment step: enqueue_to_queue (replaces enable_auto_merge + queue_enqueue_no_auto)
# ---------------------------------------------------------------------------


def test_enqueue_to_queue_uses_enqueue_pr_tool(any_recipe) -> None:
    """The unified enrollment step must use the enqueue_pr tool, not run_cmd."""
    step = any_recipe.steps["enqueue_to_queue"]
    assert step.tool == "enqueue_pr"


def test_enqueue_to_queue_passes_auto_merge_available(any_recipe) -> None:
    """enqueue_to_queue must pass context.auto_merge_available to the tool."""
    step = any_recipe.steps["enqueue_to_queue"]
    assert "auto_merge_available" in (step.with_args or {})
    assert "context.auto_merge_available" in (step.with_args or {}).get("auto_merge_available", "")


def test_reenter_merge_queue_uses_enqueue_pr_tool(any_recipe) -> None:
    """Re-entry after ejection must use enqueue_pr tool."""
    step = any_recipe.steps["reenter_merge_queue"]
    assert step.tool == "enqueue_pr"


def test_reenter_merge_queue_cheap_uses_enqueue_pr_tool(any_recipe) -> None:
    """Re-entry after drop must use enqueue_pr tool."""
    step = any_recipe.steps["reenter_merge_queue_cheap"]
    assert step.tool == "enqueue_pr"


def test_reenroll_stalled_pr_uses_enqueue_pr_tool(any_recipe) -> None:
    """Stall recovery must use enqueue_pr tool, not toggle_auto_merge."""
    step = any_recipe.steps["reenroll_stalled_pr"]
    assert step.tool == "enqueue_pr"


def test_no_gh_pr_merge_in_queue_enrollment_steps(any_recipe) -> None:
    """Every enrollment step must use enqueue_pr (not run_cmd with gh pr merge)."""
    enrollment_steps = [
        "enqueue_to_queue",
        "reenter_merge_queue",
        "reenter_merge_queue_cheap",
        "reenroll_stalled_pr",
    ]
    for step_name in enrollment_steps:
        step = any_recipe.steps.get(step_name)
        if step is None:
            continue
        assert step.tool == "enqueue_pr", (
            f"{step_name} must use enqueue_pr tool, got {step.tool!r}"
        )
        if step.tool == "run_cmd":
            cmd = step.with_args.get("cmd", "")
            assert "gh pr merge" not in cmd, f"{step_name} must not use gh pr merge"


def test_no_enable_auto_merge_step_exists(any_recipe) -> None:
    """enable_auto_merge step must be removed (replaced by enqueue_to_queue)."""
    assert "enable_auto_merge" not in any_recipe.steps


def test_no_queue_enqueue_no_auto_step_exists(any_recipe) -> None:
    """queue_enqueue_no_auto step must be removed (merged into enqueue_to_queue)."""
    assert "queue_enqueue_no_auto" not in any_recipe.steps


# ---------------------------------------------------------------------------
# T13: Merge step failure routing — silent success degradation guards
# ---------------------------------------------------------------------------


def test_enqueue_to_queue_failure_routes_to_verify_queue_enrollment(any_recipe) -> None:
    """enqueue_to_queue on_failure must route to verify_queue_enrollment."""
    step = any_recipe.steps["enqueue_to_queue"]
    assert step.on_failure == "verify_queue_enrollment"


def test_verify_queue_enrollment_exists(any_recipe) -> None:
    assert "verify_queue_enrollment" in any_recipe.steps


def test_verify_queue_enrollment_uses_wait_for_merge_queue(any_recipe) -> None:
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.tool == "wait_for_merge_queue"


def test_verify_queue_enrollment_on_failure_escalates(any_recipe) -> None:
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.on_failure == "register_clone_unconfirmed"


def test_verify_queue_enrollment_merged_routes_to_release_issue_success(any_recipe) -> None:
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.on_result is not None
    merged_routes = [c.route for c in step.on_result.conditions if c.when and "merged" in c.when]
    assert merged_routes == ["release_issue_success"]


def test_verify_queue_enrollment_fallback_routes_to_register_clone_unconfirmed(any_recipe) -> None:
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.on_result is not None
    fallback = [c.route for c in step.on_result.conditions if c.when is None]
    assert fallback == ["register_clone_unconfirmed"]


def test_verify_queue_enrollment_ejected_ci_failure_routes_directly_to_diagnose_ci(
    any_recipe,
) -> None:
    """verify_queue_enrollment must route ejected_ci_failure directly to diagnose_ci.

    A 60s probe that already confirmed CI failure should not feed into a 900s
    wait_for_queue watch that would only route to diagnose_ci anyway.
    """
    step = any_recipe.steps["verify_queue_enrollment"]
    assert step.on_result is not None
    ejected_ci_routes = [
        c.route
        for c in step.on_result.conditions
        if c.when is not None and "ejected_ci_failure" in c.when
    ]
    assert ejected_ci_routes == ["diagnose_ci"], (
        f"verify_queue_enrollment must route ejected_ci_failure directly to diagnose_ci, "
        f"got: {ejected_ci_routes}"
    )


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_wait_for_direct_merge_on_failure_routes_to_register_clone_unconfirmed(
    recipe_name: str,
) -> None:
    """wait_for_direct_merge.on_failure must be register_clone_unconfirmed in all three recipes."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    step = recipe.steps["wait_for_direct_merge"]
    assert step.on_failure == "register_clone_unconfirmed", (
        f"{recipe_name}.yaml wait_for_direct_merge.on_failure must be "
        f"'register_clone_unconfirmed', got: {step.on_failure!r}"
    )


# ---------------------------------------------------------------------------
# T9: Full routing parity — every PRState covered (universal + family-specific)
# ---------------------------------------------------------------------------

_REQUIRED_PR_STATE_VALUES = frozenset(
    s.value for s in PRState if s not in {PRState.ERROR, PRState.NOT_ENROLLED}
)
_PR_STATE_WHEN_RE = re.compile(r"\$\{\{\s*result\.pr_state\s*\}\}\s*==\s*(\w+)")


@pytest.mark.parametrize("recipe_fixture", QUEUE_RECIPES)
def test_wait_for_queue_routing_covers_every_pr_state(recipe_fixture, request) -> None:
    """Every wait_for_merge_queue step must cover every non-error PRState."""
    recipe = request.getfixturevalue(recipe_fixture)

    # Find wait_for_merge_queue steps dynamically
    mq_steps = {
        name: step
        for name, step in recipe.steps.items()
        if step.tool == "wait_for_merge_queue"
        and step.on_result is not None
        and step.on_result.conditions
    }
    assert mq_steps, f"{recipe_fixture}: no wait_for_merge_queue step with on_result found"

    for step_name, step in mq_steps.items():
        conditions = step.on_result.conditions

        covered: set[str] = set()
        for c in conditions:
            if c.when is None:
                continue
            m = _PR_STATE_WHEN_RE.search(c.when)
            if m:
                covered.add(m.group(1))

        missing = _REQUIRED_PR_STATE_VALUES - covered
        assert not missing, (
            f"{recipe_fixture}: {step_name}.on_result is missing explicit routing arms "
            f"for PRState values: {sorted(missing)}. Every non-error PRState must have a "
            f"when condition."
        )

        # ejected_ci_failure must precede generic ejected
        whens = [c.when or "" for c in conditions]
        ci_fail_idx = next((i for i, w in enumerate(whens) if "ejected_ci_failure" in w), None)
        ejected_idx = next(
            (i for i, w in enumerate(whens) if w.strip() == "${{ result.pr_state }} == ejected"),
            None,
        )
        assert ci_fail_idx is not None, (
            f"{recipe_fixture}: {step_name} ejected_ci_failure route must exist"
        )
        assert ejected_idx is not None, f"{recipe_fixture}: {step_name} ejected route must exist"
        assert ci_fail_idx < ejected_idx, (
            f"{recipe_fixture}: {step_name} ejected_ci_failure route must appear "
            f"before generic ejected route"
        )


# ---------------------------------------------------------------------------
# Auto-discovery structural guards
# ---------------------------------------------------------------------------


def test_auto_discovery_includes_merge_prs() -> None:
    """merge-prs.yaml must appear in the auto-discovered queue recipe list."""
    assert "pmp_recipe" in QUEUE_RECIPES, (
        "merge-prs.yaml (pmp_recipe) must be in QUEUE_RECIPES — if this fails, "
        "someone removed wait_for_merge_queue from merge-prs.yaml"
    )


def test_auto_discovery_matches_known_queue_recipes() -> None:
    """Auto-discovered queue recipes must match the expected set exactly."""
    expected = {"impl_recipe", "remed_recipe", "impl_groups_recipe", "pmp_recipe"}
    actual = set(QUEUE_RECIPES)
    assert actual == expected, (
        f"QUEUE_RECIPES mismatch — expected {sorted(expected)}, got {sorted(actual)}. "
        f"Missing: {sorted(expected - actual)}, Extra: {sorted(actual - expected)}"
    )


# ---------------------------------------------------------------------------
# NOT_ENROLLED routing — all queue-capable recipes
# ---------------------------------------------------------------------------


def test_wait_for_queue_routes_not_enrolled(any_recipe) -> None:
    """wait_for_queue must have an explicit routing arm for not_enrolled."""
    step = any_recipe.steps["wait_for_queue"]
    conditions = [c.when for c in step.on_result.conditions]
    assert any("not_enrolled" in c for c in conditions if c)


def test_verify_queue_enrollment_routes_not_enrolled(any_recipe) -> None:
    """verify_queue_enrollment must route not_enrolled state."""
    step = any_recipe.steps["verify_queue_enrollment"]
    conditions = [c.when for c in step.on_result.conditions]
    assert any("not_enrolled" in c for c in conditions if c)


# ---------------------------------------------------------------------------
# any_recipe — hardcoded-origin semantic rule
# ---------------------------------------------------------------------------


def test_no_hardcoded_origin_in_run_cmd_queue_capable(any_recipe) -> None:
    """After REMOTE probe fix, run_semantic_rules must report zero hardcoded-origin-in-run-cmd."""
    from autoskillit.recipe.validator import run_semantic_rules

    findings = run_semantic_rules(any_recipe)
    violations = [f for f in findings if f.rule == "hardcoded-origin-in-run-cmd"]
    assert violations == [], (
        f"hardcoded-origin-in-run-cmd fired on {any_recipe.name}: "
        f"{[v.step_name for v in violations]}"
    )


# ---------------------------------------------------------------------------
# any_recipe — check_eject_limit
# ---------------------------------------------------------------------------


def test_check_eject_limit_step_exists_in_queue_capable(any_recipe) -> None:
    """check_eject_limit step must exist in each queue-capable recipe."""
    assert "check_eject_limit" in any_recipe.steps, (
        f"check_eject_limit step missing from {any_recipe.name}"
    )


def test_check_eject_limit_routes_to_queue_ejected_fix(any_recipe) -> None:
    """check_eject_limit default route must point to queue_ejected_fix."""
    step = any_recipe.steps["check_eject_limit"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    default_conds = [c for c in conds if c.when is None]
    assert len(default_conds) == 1, "check_eject_limit must have exactly one default route"
    assert default_conds[0].route == "queue_ejected_fix"


def test_check_eject_limit_routes_to_failure_when_exceeded(any_recipe) -> None:
    """check_eject_limit must route to release_issue_failure when EJECT_LIMIT_EXCEEDED."""
    step = any_recipe.steps["check_eject_limit"]
    assert step.on_result is not None
    conds = step.on_result.conditions
    limit_conds = [c for c in conds if c.when and "EJECT_LIMIT_EXCEEDED" in c.when]
    assert len(limit_conds) == 1
    assert limit_conds[0].route == "release_issue_failure"


def test_check_eject_limit_callable_references_counter_file(any_recipe) -> None:
    """check_eject_limit callable must receive counter_file arg under .autoskillit/temp/."""
    step = any_recipe.steps["check_eject_limit"]
    counter_file = step.with_args.get("counter_file", "")
    assert "eject_count" in counter_file, "counter_file must reference eject_count"
    assert ".autoskillit/temp" in counter_file, "counter_file must be under .autoskillit/temp"


def test_check_eject_limit_callable_uses_limit_3(any_recipe) -> None:
    """check_eject_limit callable must cap at 3 ejections."""
    step = any_recipe.steps["check_eject_limit"]
    assert step.with_args.get("max_ejects") == "3", "max_ejects must be '3'"


def test_check_eject_limit_on_result_uses_exact_eq_match(any_recipe) -> None:
    """check_eject_limit on_result must use exact == match (consistent with recipe patterns)."""
    step = any_recipe.steps["check_eject_limit"]
    assert step.on_result is not None
    limit_conds = [
        c for c in step.on_result.conditions if c.when and "EJECT_LIMIT_EXCEEDED" in c.when
    ]
    assert len(limit_conds) == 1
    assert "==" in limit_conds[0].when, "predicate must use exact == match"


def test_unbounded_cycle_severity_downgraded_by_eject_limit(any_recipe) -> None:
    """After check_eject_limit, unbounded-cycle must not fire ERROR for queue ejection cycle.

    check_eject_limit is a run_python step in _STRUCTURAL_ON_RESULT_TOOLS with an on_result
    condition routing to release_issue_failure (outside the cycle). The has_on_result_exit
    path in rules_graph.py recognises this as a structural bound and suppresses the finding
    entirely — so zero unbounded-cycle findings are expected for the queue ejection cycle.
    """
    from autoskillit.core.types import Severity
    from autoskillit.recipe.validator import run_semantic_rules

    findings = run_semantic_rules(any_recipe)
    cycle_findings = [f for f in findings if f.rule == "unbounded-cycle"]
    queue_cycle_error_findings = [
        f
        for f in cycle_findings
        if f.severity == Severity.ERROR
        and any(
            kw in f.message for kw in ("wait_for_queue", "queue_ejected_fix", "check_eject_limit")
        )
    ]
    assert queue_cycle_error_findings == [], (
        f"unbounded-cycle must not be ERROR for queue ejection cycle after check_eject_limit; "
        f"got ERROR on: {[f.step_name for f in queue_cycle_error_findings]}"
    )
