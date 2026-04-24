"""Tests for CI semantic rules: ci-polling-inline-shell and ci-failure-missing-conflict-gate."""

from __future__ import annotations

import pytest

from autoskillit.core import PRState, Severity
from autoskillit.recipe.io import _parse_step, builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for CI rule tests."""
    return Recipe(
        name="test-ci-rule",
        description="Test recipe for ci-polling-inline-shell rule.",
        version="0.2.0",
        kitchen_rules="Use wait_for_ci.",
        steps=steps,
    )


def _make_workflow(steps: dict[str, dict]) -> Recipe:
    """Helper that accepts YAML-style step dicts and constructs a Recipe."""
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Recipe(
        name="test-ci-conflict-gate",
        description="Test recipe for ci-failure-missing-conflict-gate rule.",
        version="0.2.0",
        kitchen_rules="Use conflict gates.",
        steps=parsed_steps,
    )


def test_inline_ci_polling_detected() -> None:
    """run_cmd step with gh run list/watch triggers ci-polling-inline-shell WARNING."""
    steps = {
        "ci_watch": RecipeStep(
            tool="run_cmd",
            with_args={
                "cmd": (
                    "run_id=$(gh run list --branch main --limit 1 "
                    '--json databaseId,status --jq ".[]" | head -1)\n'
                    'gh run watch "$run_id" --exit-status'
                ),
                "cwd": "/tmp",
            },
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 1
    assert ci_findings[0].severity == Severity.WARNING
    assert ci_findings[0].step_name == "ci_watch"
    assert "wait_for_ci" in ci_findings[0].message


def test_wait_for_ci_tool_not_flagged() -> None:
    """Steps using tool: wait_for_ci must not trigger ci-polling-inline-shell."""
    steps = {
        "ci_watch": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300},
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 0


def test_run_cmd_without_gh_not_flagged() -> None:
    """run_cmd steps without gh run commands must not trigger the rule."""
    steps = {
        "echo_step": RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo hello"},
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 0


def test_bundled_recipes_no_inline_ci_polling() -> None:
    """All bundled recipes must be free of ci-polling-inline-shell findings."""
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
        assert len(ci_findings) == 0, (
            f"Recipe '{yaml_path.stem}' has inline CI polling: "
            + ", ".join(f.message for f in ci_findings)
        )


# ---------------------------------------------------------------------------
# ci-failure-missing-conflict-gate rule tests
# ---------------------------------------------------------------------------


def test_ci_failure_missing_conflict_gate_fires_on_direct_resolve() -> None:
    """wait_for_ci → resolve_ci (resolve-failures) with no gate → ERROR."""
    wf = _make_workflow(
        {
            "ci_watch": {"tool": "wait_for_ci", "on_success": "done", "on_failure": "resolve_ci"},
            "resolve_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures work_dir plan_path main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" in names


def test_ci_failure_missing_gate_fires_through_diagnose_ci() -> None:
    """wait_for_ci → diagnose_ci → resolve_ci with no gate → ERROR."""
    wf = _make_workflow(
        {
            "ci_watch": {"tool": "wait_for_ci", "on_success": "done", "on_failure": "diagnose_ci"},
            "diagnose_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:diagnose-ci branch - - tests.yml"},
                "on_success": "resolve_ci",
                "on_failure": "resolve_ci",
            },
            "resolve_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures work_dir plan main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" in names


def test_ci_failure_conflict_gate_passes_with_merge_base_cmd() -> None:
    """wait_for_ci → detect_conflict(run_cmd merge-base) → resolve-failures → no error."""
    wf = _make_workflow(
        {
            "ci_watch": {
                "tool": "wait_for_ci",
                "on_success": "done",
                "on_failure": "detect_conflict",
            },
            "detect_conflict": {
                "tool": "run_cmd",
                "with": {
                    "cmd": (
                        "git fetch origin main && ! git merge-base --is-ancestor origin/main HEAD"
                    )
                },
                "on_success": "done",
                "on_failure": "resolve_ci",
            },
            "resolve_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures work plan main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" not in names


def test_ci_failure_conflict_gate_passes_with_resolve_merge_conflicts() -> None:
    """wait_for_ci → resolve-merge-conflicts gate → no error."""
    wf = _make_workflow(
        {
            "ci_watch": {
                "tool": "wait_for_ci",
                "on_success": "done",
                "on_failure": "ci_conflict_fix",
            },
            "ci_conflict_fix": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-merge-conflicts work plan main"},
                "on_success": "resolve_ci",
                "on_failure": "done",
            },
            "resolve_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:resolve-failures work plan main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" not in names


def test_ci_failure_no_resolve_failures_skips_rule() -> None:
    """wait_for_ci → diagnose_ci → cleanup (no resolve-failures) → no error."""
    wf = _make_workflow(
        {
            "ci_watch": {"tool": "wait_for_ci", "on_success": "done", "on_failure": "diagnose_ci"},
            "diagnose_ci": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:diagnose-ci branch - - tests.yml"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" not in names


def test_ci_failure_no_on_failure_skips_rule() -> None:
    """wait_for_ci with no on_failure routing → no error."""
    wf = _make_workflow(
        {
            "ci_watch": {"tool": "wait_for_ci", "on_success": "done"},
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(wf)
    names = [f.rule for f in findings]
    assert "ci-failure-missing-conflict-gate" not in names


# ---------------------------------------------------------------------------
# ci-missing-event-scope rule tests
# ---------------------------------------------------------------------------


def test_wait_for_ci_without_event_is_warning() -> None:
    """wait_for_ci step with no event param should trigger ci-missing-event-scope."""
    recipe = _make_recipe(
        {
            "ci": RecipeStep(
                tool="wait_for_ci",
                with_args={"branch": "main", "workflow": "tests.yml", "timeout_seconds": 300},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    event_findings = [f for f in findings if f.rule == "ci-missing-event-scope"]
    assert len(event_findings) == 1
    assert event_findings[0].severity == Severity.WARNING


def test_wait_for_ci_with_event_is_clean() -> None:
    """wait_for_ci step with event param should not trigger ci-missing-event-scope."""
    recipe = _make_recipe(
        {
            "ci": RecipeStep(
                tool="wait_for_ci",
                with_args={"branch": "main", "event": "push", "timeout_seconds": 300},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    event_findings = [f for f in findings if f.rule == "ci-missing-event-scope"]
    assert event_findings == []


def test_get_ci_status_without_event_is_warning() -> None:
    """get_ci_status step with no event param should trigger ci-missing-event-scope."""
    recipe = _make_recipe(
        {
            "ci": RecipeStep(
                tool="get_ci_status",
                with_args={"branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    event_findings = [f for f in findings if f.rule == "ci-missing-event-scope"]
    assert len(event_findings) == 1
    assert event_findings[0].severity == Severity.WARNING
    assert "get_ci_status" in event_findings[0].message


def test_get_ci_status_with_event_is_clean() -> None:
    """get_ci_status step with event param should not trigger ci-missing-event-scope."""
    recipe = _make_recipe(
        {
            "ci": RecipeStep(
                tool="get_ci_status",
                with_args={"branch": "main", "event": "push"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    event_findings = [f for f in findings if f.rule == "ci-missing-event-scope"]
    assert event_findings == []


# ---------------------------------------------------------------------------
# ci-hardcoded-workflow rule tests
# ---------------------------------------------------------------------------


def test_wait_for_ci_hardcoded_workflow_is_warning() -> None:
    """wait_for_ci with literal workflow value should trigger ci-hardcoded-workflow."""
    recipe = _make_recipe(
        {
            "ci": RecipeStep(
                tool="wait_for_ci",
                with_args={"branch": "main", "workflow": "tests.yml", "timeout_seconds": 300},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    wf_findings = [f for f in findings if f.rule == "ci-hardcoded-workflow"]
    assert len(wf_findings) == 1
    assert wf_findings[0].severity == Severity.WARNING


def test_wait_for_ci_no_workflow_is_clean() -> None:
    """wait_for_ci without workflow param (uses config fallback) is clean."""
    recipe = _make_recipe(
        {
            "ci": RecipeStep(
                tool="wait_for_ci",
                with_args={"branch": "main", "timeout_seconds": 300},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    wf_findings = [f for f in findings if f.rule == "ci-hardcoded-workflow"]
    assert wf_findings == []


# ---------------------------------------------------------------------------
# T11 + T13: wait-for-merge-queue-routing-covers-all-pr-states rule
# ---------------------------------------------------------------------------

_COVERAGE_RULE = "wait-for-merge-queue-routing-covers-all-pr-states"
_CONFORMANCE_RULE = "wait-for-merge-queue-routing-conforms-to-expected-targets"


def _make_mq_conditions(
    *,
    exclude: set[str] | None = None,
    fallback_route: str = "register_clone_unconfirmed",
) -> list[StepResultCondition]:
    """Build a complete wait_for_merge_queue on_result conditions list.

    Args:
        exclude: set of PRState.value strings to leave out of the explicit arms.
        fallback_route: the route for the catch-all (when=None) condition.
    """
    exclude = exclude or set()
    conditions = []
    for state in PRState:
        if state == PRState.ERROR:
            continue
        if state.value in exclude:
            continue
        conditions.append(
            StepResultCondition(
                when=f"${{{{ result.pr_state }}}} == {state.value}",
                route="some_target",
            )
        )
    conditions.append(StepResultCondition(when=None, route=fallback_route))
    return conditions


def _make_mq_step(
    *,
    exclude: set[str] | None = None,
    fallback_route: str = "register_clone_unconfirmed",
    on_failure: str = "register_clone_unconfirmed",
) -> RecipeStep:
    return RecipeStep(
        tool="wait_for_merge_queue",
        on_result=StepResultRoute(
            conditions=_make_mq_conditions(exclude=exclude, fallback_route=fallback_route)
        ),
        on_failure=on_failure,
    )


def _make_mq_recipe(
    *,
    exclude: set[str] | None = None,
    fallback_route: str = "register_clone_unconfirmed",
    on_failure: str = "register_clone_unconfirmed",
) -> Recipe:
    """Build a recipe with register_clone_unconfirmed (impl/remed family pattern)."""
    return _make_recipe(
        {
            "wait_for_queue": _make_mq_step(
                exclude=exclude, fallback_route=fallback_route, on_failure=on_failure
            ),
            "register_clone_unconfirmed": RecipeStep(action="stop", message="timeout"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )


def _make_mq_recipe_no_sentinel(
    *,
    exclude: set[str] | None = None,
    fallback_route: str = "register_clone_failure",
    on_failure: str = "register_clone_failure",
) -> Recipe:
    """Build a recipe WITHOUT register_clone_unconfirmed (e.g. merge-prs family pattern)."""
    return _make_recipe(
        {
            "wait_for_queue": _make_mq_step(
                exclude=exclude, fallback_route=fallback_route, on_failure=on_failure
            ),
            "register_clone_failure": RecipeStep(action="stop", message="failed"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )


def test_coverage_rule_flags_missing_pr_state_arm() -> None:
    """T11: coverage rule fires when an explicit PRState arm is missing."""
    recipe = _make_mq_recipe(exclude={"dropped_healthy"})
    findings = run_semantic_rules(recipe)
    coverage_findings = [f for f in findings if f.rule == _COVERAGE_RULE]
    assert len(coverage_findings) >= 1, f"Expected coverage rule finding, got: {findings}"
    assert coverage_findings[0].severity == Severity.ERROR
    assert "dropped_healthy" in coverage_findings[0].message


def test_coverage_rule_clean_when_all_pr_states_present() -> None:
    """T11 negative: coverage rule is silent when all non-error PRState values are present."""
    recipe = _make_mq_recipe()
    findings = run_semantic_rules(recipe)
    coverage_findings = [f for f in findings if f.rule == _COVERAGE_RULE]
    assert coverage_findings == [], (
        f"Expected no coverage findings for complete routing, got: {coverage_findings}"
    )


def test_conformance_rule_flags_wrong_fallback_target() -> None:
    """T11: conformance rule fires when fallback routes to wrong target."""
    recipe = _make_mq_recipe(fallback_route="register_clone_success")
    findings = run_semantic_rules(recipe)
    conformance_findings = [f for f in findings if f.rule == _CONFORMANCE_RULE]
    assert len(conformance_findings) >= 1, (
        f"Expected conformance finding for wrong fallback, got: {findings}"
    )
    assert conformance_findings[0].severity == Severity.ERROR


def test_conformance_rule_flags_wrong_on_failure_target() -> None:
    """T11: conformance rule fires when on_failure routes to wrong target."""
    recipe = _make_mq_recipe(on_failure="register_clone_success")
    findings = run_semantic_rules(recipe)
    conformance_findings = [f for f in findings if f.rule == _CONFORMANCE_RULE]
    assert len(conformance_findings) >= 1, (
        f"Expected conformance finding for wrong on_failure, got: {findings}"
    )
    assert conformance_findings[0].severity == Severity.ERROR


def test_conformance_rule_clean_when_targets_correct() -> None:
    """T11 negative: conformance rule is silent when targets are correct."""
    recipe = _make_mq_recipe()
    findings = run_semantic_rules(recipe)
    conformance_findings = [f for f in findings if f.rule == _CONFORMANCE_RULE]
    assert conformance_findings == [], (
        f"Expected no conformance findings for correct targets, got: {conformance_findings}"
    )


# ---------------------------------------------------------------------------
# Tool-presence scope gate tests (I7 fires without register_clone_unconfirmed sentinel)
# ---------------------------------------------------------------------------


def test_coverage_rule_fires_without_register_clone_unconfirmed_step() -> None:
    """I7 must fire on recipes without register_clone_unconfirmed if they have mq routing."""
    recipe = _make_mq_recipe_no_sentinel(exclude={"dropped_healthy", "stalled"})
    findings = run_semantic_rules(recipe)
    coverage_findings = [f for f in findings if f.rule == _COVERAGE_RULE]
    assert len(coverage_findings) >= 1, (
        f"Expected coverage finding (no sentinel step), got: {findings}"
    )
    assert coverage_findings[0].severity == Severity.ERROR
    assert "dropped_healthy" in coverage_findings[0].message
    assert "stalled" in coverage_findings[0].message


def test_coverage_rule_clean_without_sentinel_when_all_states_present() -> None:
    """I7 is silent for a non-sentinel recipe when all PRState values are present."""
    recipe = _make_mq_recipe_no_sentinel()
    findings = run_semantic_rules(recipe)
    coverage_findings = [f for f in findings if f.rule == _COVERAGE_RULE]
    assert coverage_findings == [], (
        f"Expected no coverage findings for complete routing without sentinel, "
        f"got: {coverage_findings}"
    )


def test_coverage_rule_silent_for_non_queue_recipe() -> None:
    """I7 must not fire on recipes that have no wait_for_merge_queue step."""
    recipe = _make_recipe(
        {
            "do_work": RecipeStep(tool="run_skill", on_success="done"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    coverage_findings = [f for f in findings if f.rule == _COVERAGE_RULE]
    assert coverage_findings == [], (
        f"Non-queue recipe should produce no I7 findings, got: {coverage_findings}"
    )


def test_conformance_rule_silent_without_sentinel() -> None:
    """I8 must NOT fire on recipes without register_clone_unconfirmed (family-specific rule)."""
    recipe = _make_mq_recipe_no_sentinel(
        fallback_route="register_clone_failure",
        on_failure="register_clone_failure",
    )
    findings = run_semantic_rules(recipe)
    conformance_findings = [f for f in findings if f.rule == _CONFORMANCE_RULE]
    assert conformance_findings == [], (
        f"I8 should be silent for non-sentinel recipes, got: {conformance_findings}"
    )
