"""Cross-recipe structural tests: ci_event/branch coherence for all wait_for_ci steps.

Every wait_for_ci step must use a ci_event that was derived for the same branch
it watches. This prevents the class of bug where ci_event is derived for
inputs.base_branch (main) but reused for pr-batch/*, feature, or worktree branches
where different CI trigger rules apply.
"""

from __future__ import annotations

import re

import pytest
import yaml

from autoskillit.recipe.io import builtin_recipes_dir

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
    with open(request.param) as f:
        return request.param.stem, yaml.safe_load(f)


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
