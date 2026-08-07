"""Contract tests: zero-change circuit breaker in implementation/remediation recipes."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

RECIPES_DIR = Path(__file__).parents[2] / "src" / "autoskillit" / "recipes"


def _load(name: str) -> dict:
    return load_yaml(RECIPES_DIR / f"{name}.yaml")


def _assert_proceed_routes_to_check_changes(step: dict, *, label: str) -> None:
    proceed_routes = [
        entry.get("route")
        for entry in step.get("on_result", [])
        if "proceed" in str(entry.get("when", ""))
    ]
    assert proceed_routes == ["check_changes"], (
        f"{label} proceed result must route to check_changes, got {proceed_routes!r}"
    )


def test_implementation_recipe_has_change_check_after_implement() -> None:
    """implement must route directly to the zero-change check."""
    data = _load("implementation")
    implement_step = data["steps"]["implement"]
    _assert_proceed_routes_to_check_changes(implement_step, label="implement")


def test_implementation_recipe_check_changes_routes_false_to_close() -> None:
    """check_changes step must route has_changes=false to close_issue_no_changes."""
    data = _load("implementation")
    assert "check_changes" in data["steps"], "check_changes step must exist"
    step = data["steps"]["check_changes"]
    on_result = step.get("on_result", [])
    false_routes = [r.get("route") for r in on_result if "false" in r.get("when", "")]
    assert "close_issue_no_changes" in false_routes, (
        f"check_changes must route has_changes=false to close_issue_no_changes, got: {on_result}"
    )


def test_retry_worktree_proceed_exits_route_to_check_changes() -> None:
    """retry_worktree proceed and context-limit exits must route to check_changes."""
    data = _load("implementation")
    step = data["steps"]["retry_worktree"]

    _assert_proceed_routes_to_check_changes(step, label="retry_worktree")

    assert step.get("on_context_limit") == "check_changes", (
        f"retry_worktree.on_context_limit must be 'check_changes', "
        f"got {step.get('on_context_limit')!r}"
    )


def test_remediation_recipe_has_same_circuit_breaker() -> None:
    """remediation.yaml must have the same zero-change circuit breaker as implementation."""
    data = _load("remediation")

    implement_step = data["steps"]["implement"]
    _assert_proceed_routes_to_check_changes(implement_step, label="remediation implement")

    assert "check_changes" in data["steps"], "remediation check_changes step must exist"

    retry_step = data["steps"]["retry_worktree"]
    _assert_proceed_routes_to_check_changes(retry_step, label="remediation retry_worktree")

    assert retry_step.get("on_context_limit") == "check_changes", (
        f"remediation retry_worktree.on_context_limit must be 'check_changes', "
        f"got {retry_step.get('on_context_limit')!r}"
    )


def test_close_issue_no_changes_passes_target_branch() -> None:
    """close_issue_no_changes must pass target_branch to enable branch-aware staging."""
    for name in ("implementation", "remediation"):
        data = _load(name)
        step = data["steps"]["close_issue_no_changes"]
        with_args = step.get("with", {})
        assert "target_branch" in with_args, (
            f"{name}: close_issue_no_changes.with must include target_branch, got: {with_args}"
        )
        assert with_args["target_branch"] == "${{ inputs.base_branch }}", (
            f"{name}: close_issue_no_changes.with.target_branch must be "
            f"'${{{{ inputs.base_branch }}}}', got: {with_args['target_branch']!r}"
        )


def test_close_issue_no_changes_routes_to_register_clone_no_changes() -> None:
    """close_issue_no_changes must route to register_clone_no_changes for distinct terminal."""
    for name in ("implementation", "remediation"):
        data = _load(name)
        step = data["steps"]["close_issue_no_changes"]
        assert step.get("on_success") == "register_clone_no_changes", (
            f"{name}: close_issue_no_changes.on_success must be 'register_clone_no_changes', "
            f"got {step.get('on_success')!r}"
        )
        assert step.get("on_failure") == "register_clone_no_changes", (
            f"{name}: close_issue_no_changes.on_failure must be 'register_clone_no_changes', "
            f"got {step.get('on_failure')!r}"
        )


def test_close_issue_already_done_routes_to_distinct_terminal() -> None:
    """close_issue_already_done must route to register_clone_already_done for distinct terminal."""
    for name in ("implementation", "remediation"):
        data = _load(name)
        step = data["steps"]["close_issue_already_done"]
        assert step.get("on_success") == "register_clone_already_done", (
            f"{name}: close_issue_already_done.on_success must be 'register_clone_already_done', "
            f"got {step.get('on_success')!r}"
        )
        assert step.get("on_failure") == "register_clone_already_done", (
            f"{name}: close_issue_already_done.on_failure must be 'register_clone_already_done', "
            f"got {step.get('on_failure')!r}"
        )


def test_done_step_message_says_implementation_complete() -> None:
    """done step must use underscore-convention reason string."""
    expected = {
        "implementation": "implementation_complete",
        "remediation": "remediation_complete",
    }
    for name, reason in expected.items():
        data = _load(name)
        step = data["steps"]["done"]
        assert f'"{reason}"' in step["message"], (
            f"{name}: done.message must contain '\"{reason}\"', got: {step['message']!r}"
        )
