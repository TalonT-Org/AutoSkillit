"""Cross-recipe structural tests: ci_event/branch coherence for all wait_for_ci steps.

Every wait_for_ci step must use a ci_event that was derived for the same branch
it watches. This prevents the class of bug where ci_event is derived for
inputs.base_branch (main) but reused for pr-batch/*, feature, or worktree branches
where different CI trigger rules apply.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.recipe.io import builtin_recipes_dir

from .conftest import (
    PRIMARY_CI_EVENT_KEYS,
    build_reverse_graph,
    has_wait_for_ci_predecessor,
    reaches_wait_for_ci,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_TEMPLATE_RE = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")


def _extract_ref(template: str) -> str | None:
    """Extract the context/result reference from a ${{ ... }} template string."""
    m = _TEMPLATE_RE.search(template)
    return m.group(1) if m else None


def _find_capture_source(steps: dict, capture_name: str) -> tuple[str, dict] | None:
    """Find the step that captures a given context variable name."""
    for step_name, step_data in steps.items():
        capture = step_data.get("capture") or {}
        if capture_name in capture:
            return step_name, step_data
    return None


RECIPE_FILES = sorted(builtin_recipes_dir().glob("*.yaml"))


@pytest.fixture(params=RECIPE_FILES, ids=lambda p: p.stem)
def recipe_data(request):
    return request.param.stem, load_yaml(request.param)


def test_wait_for_ci_event_branch_coherence(recipe_data) -> None:
    """For each wait_for_ci step, the event: must come from a check_repo_merge_state
    step that used the same branch variable as the wait_for_ci step's branch: field,
    OR the step must have its own dedicated ci_event derivation step."""
    recipe_name, data = recipe_data
    steps = data.get("steps") or {}

    wait_steps = {
        name: step for name, step in steps.items() if (step.get("tool") or "") == "wait_for_ci"
    }

    if not wait_steps:
        pytest.skip(f"{recipe_name}: no wait_for_ci steps")

    violations = []

    for step_name, step in wait_steps.items():
        with_args = step.get("with") or {}
        event_template = with_args.get("event", "")
        branch_template = with_args.get("branch", "")

        event_ref = _extract_ref(event_template)
        branch_ref = _extract_ref(branch_template)

        if not event_ref or not branch_ref:
            continue

        event_var = event_ref.split(".")[-1] if "." in event_ref else event_ref

        source = _find_capture_source(steps, event_var)
        if source is None:
            continue

        source_name, source_step = source
        source_tool = source_step.get("tool") or source_step.get("python") or ""
        if (
            "check_repo_merge_state" not in source_tool
            and "fetch_repo_merge_state" not in source_tool
        ):
            continue

        source_with = source_step.get("with") or {}
        source_branch_template = source_with.get("branch", "")
        source_branch_ref = _extract_ref(source_branch_template)

        if source_branch_ref and source_branch_ref != branch_ref:
            violations.append(
                f"{step_name}: event '{event_var}' derived from {source_name} "
                f"(branch: {source_branch_ref}) but wait_for_ci watches "
                f"branch: {branch_ref}"
            )

    assert not violations, f"{recipe_name}: ci_event/branch mismatch detected:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_wait_for_ci_steps_have_remote_url(recipe_data) -> None:
    """Every wait_for_ci step should include remote_url to prevent file:// fallback."""
    recipe_name, data = recipe_data
    steps = data.get("steps") or {}

    wait_steps = {
        name: step for name, step in steps.items() if (step.get("tool") or "") == "wait_for_ci"
    }

    if not wait_steps:
        pytest.skip(f"{recipe_name}: no wait_for_ci steps")

    missing = []
    for step_name, step in wait_steps.items():
        with_args = step.get("with") or {}
        if "remote_url" not in with_args:
            missing.append(step_name)

    assert not missing, f"{recipe_name}: wait_for_ci steps missing remote_url: {missing}"


def test_ci_event_capture_has_ci_applicable_guard(recipe_data) -> None:
    """Steps capturing a primary ci_event from check_repo_merge_state must also
    capture the corresponding ci_applicable field when they route to wait_for_ci.

    Only checks ci_event and conflict_ci_event — secondary variants like
    pre_enqueue_ci_event and batch_ci_event have independent derivation chains.
    Excludes re-derivation steps downstream of a wait_for_ci step and
    diagnostic-only captures that don't feed into a CI wait chain.
    """
    recipe_name, data = recipe_data
    steps = data.get("steps") or {}
    reverse_graph = build_reverse_graph(steps)

    ci_event_capture_steps = []
    for step_name, step in steps.items():
        tool = step.get("tool", "")
        if "check_repo_merge_state" not in tool:
            continue
        capture = step.get("capture") or {}
        if not (capture.keys() & PRIMARY_CI_EVENT_KEYS):
            continue
        if has_wait_for_ci_predecessor(steps, step_name, reverse_graph):
            continue
        if not reaches_wait_for_ci(steps, step_name):
            continue
        ci_event_capture_steps.append(step_name)

    if not ci_event_capture_steps:
        pytest.skip(f"{recipe_name}: no check_repo_merge_state steps feeding wait_for_ci")

    violations = []
    for step_name in ci_event_capture_steps:
        step = steps[step_name]
        capture = step.get("capture") or {}
        has_applicable_capture = any("ci_applicable" in k for k in capture)
        if not has_applicable_capture:
            violations.append(f"{step_name}: captures ci_event but not ci_applicable")

    assert not violations, f"{recipe_name}: ci_event capture without ci_applicable:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_check_repo_merge_state_with_ci_applicable_passes_base_branch(recipe_data) -> None:
    """Every check_repo_merge_state step that captures ci_applicable must also pass base_branch.

    ci_applicable requires base_branch to detect pull_request triggers. Steps that
    capture ci_applicable without passing base_branch will silently get ci_applicable=False
    for pull_request-only repos, causing false ci_watch skips.
    """
    recipe_name, data = recipe_data
    steps = data.get("steps") or {}

    violations = []
    for step_name, step in steps.items():
        tool = step.get("tool", "")
        if "check_repo_merge_state" not in tool:
            continue
        capture = step.get("capture") or {}
        if not any("ci_applicable" in k for k in capture):
            continue
        with_args = step.get("with") or {}
        if "base_branch" not in with_args:
            violations.append(step_name)

    assert not violations, (
        f"{recipe_name}: check_repo_merge_state steps capture ci_applicable "
        f"but omit base_branch: {violations}"
    )
