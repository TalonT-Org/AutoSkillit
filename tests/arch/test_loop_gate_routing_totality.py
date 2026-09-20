"""Every bundled loop guard must route its distinct outcomes separately."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_RECIPES = Path(__file__).resolve().parents[2] / "src/autoskillit/recipes"
_LOOP = "autoskillit.smoke_utils.check_loop_iteration"
_PROGRESS = "autoskillit.smoke_utils.check_loop_with_progress"
_AUDIT = "autoskillit.smoke_utils.check_audit_remediation_outcome"

_LOOP_SITES = {
    "implementation": (
        "check_active_trigger_loop check_audit_integrity_retry check_ci_loop "
        "check_ci_post_queue_loop check_ci_rebase_loop check_ci_timed_out_loop "
        "check_dequeue_retry_loop check_dirty_main_retry check_flake_loop check_merge_fix_loop "
        "check_merge_rebase_loop check_merge_test_fix_loop check_ref_push_loop "
        "check_replan_iteration check_stall_loop check_test_fix_loop"
    ),
    "implementation-groups": (
        "check_active_trigger_loop check_audit_integrity_retry check_ci_loop "
        "check_ci_post_queue_loop check_ci_rebase_loop check_ci_timed_out_loop "
        "check_dequeue_retry_loop check_dirty_main_retry check_flake_loop "
        "check_group_plan_iteration check_merge_fix_loop "
        "check_merge_rebase_loop check_merge_test_fix_loop check_next_iteration "
        "check_ref_push_loop check_replan_iteration check_stall_loop check_test_fix_loop"
    ),
    "merge-prs": (
        "check_audit_integrity_retry check_ci_post_queue_loop check_ci_watch_pr_loop "
        "check_conflict_ci_loop check_dequeue_retry_loop "
        "check_pr_merge_loop check_queue_stall_loop "
        "check_test_fix_loop guard_ci_skip_conflict"
    ),
    "remediation": (
        "check_active_trigger_loop check_audit_integrity_retry check_ci_loop "
        "check_ci_post_queue_loop check_ci_rebase_loop check_ci_timed_out_loop "
        "check_dequeue_retry_loop check_dirty_main_retry check_flake_loop check_merge_fix_loop "
        "check_merge_rebase_loop check_merge_test_fix_loop check_ref_push_loop "
        "check_ref_push_loop_pre_remediation check_replan_iteration check_stall_loop "
        "check_test_fix_loop"
    ),
    "research": (
        "check_audit_integrity_retry check_design_review_loop "
        "check_implement_fix_loop check_phase_loop check_run_fix_loop"
    ),
    "research-design": "check_design_review_loop",
    "implement-findings": "check_group_iteration",
    "research-implement": (
        "check_audit_integrity_retry check_implement_fix_loop check_phase_loop check_run_fix_loop"
    ),
}
_AUDIT_SITES = {
    "implementation": "check_audit_remediation_loop",
    "implementation-groups": "check_audit_remediation_loop",
    "merge-prs": "check_audit_remediation_loop",
    "remediation": "check_audit_remediation_loop",
    "research": "check_audit_retry_loop",
    "research-implement": "check_audit_retry_loop",
}
_EXPECTED_GATE_SITES = (
    frozenset(
        (recipe, step, _LOOP) for recipe, steps in _LOOP_SITES.items() for step in steps.split()
    )
    | frozenset(
        (recipe, step, _AUDIT) for recipe, steps in _AUDIT_SITES.items() for step in steps.split()
    )
    | {("planner", "check_refine_loop", _PROGRESS)}
)

_OUTCOME_CONDITIONS = {
    _LOOP: ("${{ result.max_exceeded }} == true", None),
    _PROGRESS: (
        "${{ result.zero_progress }} == true",
        "${{ result.max_exceeded }} == true",
        None,
    ),
    _AUDIT: tuple(
        f"${{{{ result.outcome }}}} == {outcome}"
        for outcome in (
            "PROGRESSING",
            "STUCK_REPEATING",
            "EXHAUSTED",
            "AWAITING_DECISION",
            "INTEGRITY_FAULT",
        )
    ),
}


def _gate_errors(steps: dict[str, dict], gate_name: str, callable_name: str) -> list[str]:
    gate = steps[gate_name]
    conditions = gate.get("on_result", [])
    routes = {condition.get("when"): condition.get("route") for condition in conditions}
    errors: list[str] = []
    outcomes = _OUTCOME_CONDITIONS[callable_name]
    selected = []
    for condition in outcomes:
        target = routes.get(condition)
        if target is None:
            errors.append(f"{gate_name} lacks {condition!r}")
        elif target not in steps:
            errors.append(f"{gate_name} routes to unknown step {target!r}")
        else:
            selected.append(target)
    if len(set(selected)) != len(selected):
        errors.append(f"{gate_name} merges distinguishable outcomes")
    fallback = routes.get(None)
    if fallback is not None and selected and fallback == selected[0]:
        errors.append(f"{gate_name} fallback shares its success route")
    return errors


def test_bundled_gate_inventory_and_routes_are_total() -> None:
    actual: set[tuple[str, str, str]] = set()
    errors: list[str] = []
    for path in _RECIPES.glob("*.yaml"):
        data = load_yaml(path.read_text(encoding="utf-8"))
        steps = data.get("steps", {})
        for name, step in steps.items():
            callable_name = step.get("with", {}).get("callable")
            if callable_name not in _OUTCOME_CONDITIONS:
                continue
            actual.add((path.stem, name, callable_name))
            errors.extend(
                f"{path.stem}: {error}" for error in _gate_errors(steps, name, callable_name)
            )
    assert actual == _EXPECTED_GATE_SITES
    assert errors == []


def test_gate_guard_catches_duplicate_fallback_and_unknown_target() -> None:
    steps = {
        "gate": {
            "on_result": [
                {"when": "${{ result.max_exceeded }} == true", "route": "success"},
                {"route": "success"},
            ]
        },
        "success": {"action": "stop"},
    }
    assert any("merges distinguishable" in error for error in _gate_errors(steps, "gate", _LOOP))
    assert any("fallback shares" in error for error in _gate_errors(steps, "gate", _LOOP))
    steps["gate"]["on_result"][0]["route"] = "missing"
    assert any("unknown step" in error for error in _gate_errors(steps, "gate", _LOOP))
