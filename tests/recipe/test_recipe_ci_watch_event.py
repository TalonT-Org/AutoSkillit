"""Structural tests: primary ci_watch steps must have event: and be preceded by ci_event derivation."""
from __future__ import annotations

import pytest
import yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_FILES = [
    "src/autoskillit/recipes/implementation.yaml",
    "src/autoskillit/recipes/remediation.yaml",
    "src/autoskillit/recipes/implementation-groups.yaml",
]


@pytest.mark.parametrize("recipe_path", RECIPE_FILES)
def test_ci_watch_step_has_event_parameter(recipe_path: str) -> None:
    """Primary ci_watch steps must pass event: to prevent branch-event mismatch."""
    with open(recipe_path) as f:
        recipe = yaml.safe_load(f)
    steps = recipe["steps"]
    assert "ci_watch" in steps, f"{recipe_path}: no ci_watch step found"
    ci_watch = steps["ci_watch"]
    with_args = ci_watch.get("with") or {}
    assert "event" in with_args, (
        f"{recipe_path}: ci_watch missing event: parameter. "
        f"Feature branches excluded from push triggers will see no_runs timeout."
    )


@pytest.mark.parametrize("recipe_path", RECIPE_FILES)
def test_check_repo_ci_event_step_exists_before_ci_watch(recipe_path: str) -> None:
    """Each recipe must have a check_repo_ci_event step that routes to ci_watch."""
    with open(recipe_path) as f:
        recipe = yaml.safe_load(f)
    steps = recipe["steps"]
    assert "check_repo_ci_event" in steps, (
        f"{recipe_path}: missing check_repo_ci_event step. "
        f"ci_event must be derived before ci_watch runs."
    )
    early_step = steps["check_repo_ci_event"]
    capture = early_step.get("capture") or {}
    assert "ci_event" in capture, (
        f"{recipe_path}: check_repo_ci_event must capture ci_event into context"
    )
    assert early_step.get("on_success") == "ci_watch", (
        f"{recipe_path}: check_repo_ci_event.on_success must be ci_watch"
    )
    assert early_step.get("on_failure") == "ci_watch", (
        f"{recipe_path}: check_repo_ci_event.on_failure must be ci_watch (non-blocking)"
    )
