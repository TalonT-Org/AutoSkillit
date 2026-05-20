"""Structural tests: ci_applicable routing guards for all wait_for_ci routing chains.

Every recipe that captures ci_event from check_repo_merge_state must also capture
ci_applicable and have an action:route step that checks it before reaching
wait_for_ci. This prevents 600s timeout waste when no CI workflows apply.
"""

from __future__ import annotations

import re

import pytest
import yaml

from autoskillit.recipe.io import builtin_recipes_dir

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_CI_APPLICABLE_RE = re.compile(r"ci_applicable")

RECIPE_FILES = sorted(builtin_recipes_dir().glob("*.yaml"))


@pytest.fixture(params=RECIPE_FILES, ids=lambda p: p.stem)
def recipe_data(request):
    with open(request.param) as f:
        return request.param.stem, yaml.safe_load(f)


_PRIMARY_CI_EVENT_KEYS = {"ci_event", "conflict_ci_event"}


def _find_ci_event_capture_steps(steps: dict) -> list[tuple[str, dict]]:
    """Find steps that capture a primary ci_event from check_repo_merge_state.

    Only matches ci_event and conflict_ci_event — not secondary variants like
    pre_enqueue_ci_event, batch_ci_event, etc. which have independent derivation
    chains.
    """
    result = []
    for step_name, step in steps.items():
        tool = step.get("tool", "")
        if "check_repo_merge_state" not in tool:
            continue
        capture = step.get("capture") or {}
        if capture.keys() & _PRIMARY_CI_EVENT_KEYS:
            result.append((step_name, step))
    return result


def _reaches_wait_for_ci(steps: dict, start: str, depth: int = 5) -> bool:
    """BFS from start to check if wait_for_ci is reachable within depth hops."""
    visited: set[str] = set()
    queue = [start]
    for _ in range(depth):
        next_queue: list[str] = []
        for node in queue:
            if node in visited:
                continue
            visited.add(node)
            node_step = steps.get(node, {})
            if node_step.get("tool") == "wait_for_ci":
                return True
            for key in ("on_success", "on_failure"):
                target = node_step.get(key)
                if target and target in steps and target not in visited:
                    next_queue.append(target)
            for cond in node_step.get("on_result", []):
                target = cond.get("route")
                if target and target in steps and target not in visited:
                    next_queue.append(target)
        queue = next_queue
        if not queue:
            break
    return False


def _has_ci_applicable_route(steps: dict, from_step: str) -> bool:
    """Check if there's an action:route step that checks ci_applicable on the path."""
    step = steps.get(from_step, {})
    for target_key in ("on_success", "on_failure"):
        target = step.get(target_key)
        if target and target in steps:
            route_step = steps[target]
            if route_step.get("action") == "route":
                on_result = route_step.get("on_result", [])
                for condition in on_result:
                    when = condition.get("when", "")
                    if _CI_APPLICABLE_RE.search(when):
                        return True
    return False


def test_ci_event_capture_steps_have_ci_applicable_routing(recipe_data) -> None:
    """Steps capturing ci_event must route through a ci_applicable guard."""
    recipe_name, data = recipe_data
    steps = data.get("steps") or {}

    capture_steps = _find_ci_event_capture_steps(steps)
    if not capture_steps:
        pytest.skip(f"{recipe_name}: no check_repo_merge_state steps capturing ci_event")

    violations = []
    for step_name, _step in capture_steps:
        if not _reaches_wait_for_ci(steps, step_name):
            continue
        if not _has_ci_applicable_route(steps, step_name):
            violations.append(step_name)

    assert not violations, (
        f"{recipe_name}: check_repo_merge_state steps capturing ci_event lack "
        f"ci_applicable routing guard: {violations}. Insert an action:route step "
        f"that checks ci_applicable between the capture and wait_for_ci."
    )


def test_merge_prs_routes_around_ci_wait_when_not_applicable(recipe_data) -> None:
    """merge-prs must have ci_applicable routing for conflict CI path."""
    recipe_name, data = recipe_data
    if recipe_name != "merge-prs":
        pytest.skip("merge-prs only")
    steps = data.get("steps") or {}
    assert "route_conflict_ci" in steps, "merge-prs must have route_conflict_ci step"
    route_step = steps["route_conflict_ci"]
    assert route_step.get("action") == "route"
    on_result = route_step.get("on_result", [])
    has_false_guard = any(
        "ci_applicable" in (c.get("when", "") or "") and "false" in (c.get("when", "") or "")
        for c in on_result
    )
    assert has_false_guard, "route_conflict_ci must check ci_applicable == false"


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "implementation-groups", "remediation"],
)
def test_impl_recipes_route_around_ci_wait_when_not_applicable(recipe_name) -> None:
    """implementation/implementation-groups/remediation must have ci_applicable routing."""
    recipe_path = builtin_recipes_dir() / f"{recipe_name}.yaml"
    with open(recipe_path) as f:
        data = yaml.safe_load(f)
    steps = data.get("steps") or {}
    assert "route_ci_applicable" in steps, f"{recipe_name} must have route_ci_applicable step"
    route_step = steps["route_ci_applicable"]
    assert route_step.get("action") == "route"
    on_result = route_step.get("on_result", [])
    has_false_guard = any(
        "ci_applicable" in (c.get("when", "") or "") and "false" in (c.get("when", "") or "")
        for c in on_result
    )
    assert has_false_guard, f"{recipe_name}: route_ci_applicable must check ci_applicable == false"
