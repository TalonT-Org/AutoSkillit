"""Contract tests: zero-change circuit breaker in implementation/remediation recipes."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

RECIPES_DIR = Path(__file__).parents[2] / "src" / "autoskillit" / "recipes"


def _load(name: str) -> dict:
    return yaml.safe_load((RECIPES_DIR / f"{name}.yaml").read_text())


def test_implementation_recipe_has_change_check_after_implement() -> None:
    """implement step must route on_success to check_changes, not directly to test."""
    data = _load("implementation")
    implement_step = data["steps"]["implement"]
    assert implement_step.get("on_success") == "check_changes", (
        f"implement.on_success must be 'check_changes', got {implement_step.get('on_success')!r}"
    )


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


def test_retry_worktree_all_exits_route_to_check_changes() -> None:
    """retry_worktree: all on_result branches and on_context_limit must route to check_changes."""
    data = _load("implementation")
    step = data["steps"]["retry_worktree"]

    on_result = step.get("on_result", [])
    for entry in on_result:
        assert entry.get("route") == "check_changes", (
            f"retry_worktree on_result entry must route to check_changes, got: {entry}"
        )

    assert step.get("on_context_limit") == "check_changes", (
        f"retry_worktree.on_context_limit must be 'check_changes', "
        f"got {step.get('on_context_limit')!r}"
    )


def test_remediation_recipe_has_same_circuit_breaker() -> None:
    """remediation.yaml must have the same zero-change circuit breaker as implementation."""
    data = _load("remediation")

    implement_step = data["steps"]["implement"]
    assert implement_step.get("on_success") == "check_changes", (
        f"remediation implement.on_success must be 'check_changes', "
        f"got {implement_step.get('on_success')!r}"
    )

    assert "check_changes" in data["steps"], "remediation check_changes step must exist"

    retry_step = data["steps"]["retry_worktree"]
    on_result = retry_step.get("on_result", [])
    for entry in on_result:
        assert entry.get("route") == "check_changes", (
            f"remediation retry_worktree on_result entry must route to check_changes: {entry}"
        )

    assert retry_step.get("on_context_limit") == "check_changes", (
        f"remediation retry_worktree.on_context_limit must be 'check_changes', "
        f"got {retry_step.get('on_context_limit')!r}"
    )
