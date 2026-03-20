"""Guards against orchestrator anti-patterns re-emerging in bundled recipes."""

from __future__ import annotations

import pytest
import yaml

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe


def _all_bundled_recipes():
    return list(builtin_recipes_dir().glob("*.yaml"))


@pytest.mark.parametrize("recipe_path", _all_bundled_recipes(), ids=lambda p: p.stem)
def test_all_run_cmd_steps_have_step_name(recipe_path):
    """AP5: Every run_cmd step in every bundled recipe must declare step_name in with:.
    Unnamed run_cmd calls are invisible to pipeline reports and timing instrumentation.
    """
    raw = yaml.safe_load(recipe_path.read_text())
    steps = raw.get("steps", {})
    violations = []
    for step_name, step_data in steps.items():
        if step_data.get("tool") == "run_cmd":
            with_args = step_data.get("with", {})
            if "step_name" not in with_args:
                violations.append(f"{recipe_path.stem}.{step_name}")
    assert not violations, "run_cmd steps missing step_name:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_implementation_ci_failure_routes_to_diagnose_ci():
    """AP1: implementation.yaml ci_watch failure must route through detect_ci_conflict gate.

    After Part B, ci_watch routes to detect_ci_conflict (stale-base triage), which then
    routes to either ci_conflict_fix (stale base) or diagnose_ci (real failure). This ensures
    diagnose_ci is still reachable for real CI failures while stale-base failures are handled
    separately, rather than routing directly to resolve_ci.
    """
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    # ci_watch on_failure must route to detect_ci_conflict gate (not resolve_ci directly)
    ci_watch = recipe.steps["ci_watch"]
    assert ci_watch.on_failure == "detect_ci_conflict", (
        "ci_watch.on_failure must route to detect_ci_conflict gate, not directly to resolve_ci"
    )
    # detect_ci_conflict must exist and route real failures to diagnose_ci
    assert "detect_ci_conflict" in recipe.steps
    gate = recipe.steps["detect_ci_conflict"]
    assert gate.on_failure == "diagnose_ci", (
        "detect_ci_conflict.on_failure must route to diagnose_ci for real CI failures"
    )
    # diagnose_ci must exist and call run_skill
    assert "diagnose_ci" in recipe.steps
    diag = recipe.steps["diagnose_ci"]
    assert diag.tool == "run_skill"
    # diagnose_ci must capture diagnosis_path
    assert "diagnosis_path" in diag.capture


def test_implementation_groups_ci_failure_routes_to_diagnose_ci():
    """AP1: implementation-groups.yaml ci_watch failure must route through detect_ci_conflict."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")
    assert recipe.steps["ci_watch"].on_failure == "detect_ci_conflict", (
        "ci_watch.on_failure must route to detect_ci_conflict gate, not directly to diagnose_ci"
    )
    # detect_ci_conflict must route real failures to diagnose_ci
    assert "detect_ci_conflict" in recipe.steps
    gate = recipe.steps["detect_ci_conflict"]
    assert gate.on_failure == "diagnose_ci"
    # diagnose_ci must exist and call run_skill
    assert "diagnose_ci" in recipe.steps
    diag = recipe.steps["diagnose_ci"]
    assert diag.tool == "run_skill"
    assert "diagnosis_path" in diag.capture


def test_implementation_create_branch_uses_create_unique_branch_tool():
    """AP3: create_branch step must use create_unique_branch MCP tool, not run_cmd."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    step = recipe.steps["create_branch"]
    assert step.tool == "create_unique_branch", (
        "create_branch must use create_unique_branch tool (not run_cmd bash script)"
    )


def test_merge_prs_no_run_cmd_push():
    """AP2: merge-prs.yaml push_integration_branch must use push_to_remote, not run_cmd."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    step = recipe.steps["push_integration_branch"]
    assert step.tool == "push_to_remote", (
        "push_integration_branch must use push_to_remote MCP tool, not run_cmd"
    )


def test_merge_prs_has_no_loop_push_kitchen_rule():
    """AP2: merge-prs.yaml push_to_remote must only appear in the designated push steps."""
    raw = yaml.safe_load((builtin_recipes_dir() / "merge-prs.yaml").read_text())
    steps = raw.get("steps", {})
    push_steps = {name for name, step in steps.items() if step.get("tool") == "push_to_remote"}
    unexpected = push_steps - {
        "publish_integration_branch",
        "push_integration_branch",
        "re_push_review_integration",  # authorized: re-pushes after review fixes
        "push_ejected_fix",  # authorized: pushes conflict-resolved ejected PR branch back
    }
    assert not unexpected, (
        f"push_to_remote found in unexpected steps (loop pushes are prohibited): {unexpected}"
    )
