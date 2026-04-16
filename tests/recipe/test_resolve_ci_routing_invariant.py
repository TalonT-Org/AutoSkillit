"""Routing invariant: resolve_ci / resolve_review must use verdict-gated on_result.

Every step invoking resolve-failures or resolve-review across the three main
pipeline recipes (implementation.yaml, implementation-groups.yaml, remediation.yaml)
must route via on_result: with at least one condition referencing result.verdict.
Unconditional on_success: re_push is rejected.
"""

from __future__ import annotations

import pytest

from autoskillit.core import pkg_root
from autoskillit.recipe.io import load_recipe

pytestmark = [pytest.mark.layer("recipe")]

_RECIPES_DIR = pkg_root() / "recipes"
_PIPELINE_RECIPES = [
    "implementation.yaml",
    "implementation-groups.yaml",
    "remediation.yaml",
]

_CI_FIX_SKILLS = {"resolve-failures", "resolve-review"}


def _has_verdict_on_result(step) -> bool:
    """Return True if the step uses on_result: with at least one verdict-reference condition."""
    if step.on_result is None:
        return False
    for cond in step.on_result.conditions or []:
        if cond.when and "result.verdict" in cond.when:
            return True
    return False


def _step_reaches_push(recipe, start_name: str, max_hops: int = 5) -> bool:
    """Return True if push_to_remote is reachable from start_name within max_hops."""
    visited: set[str] = set()
    queue = [(start_name, 0)]
    while queue:
        name, hops = queue.pop(0)
        if name in visited or hops > max_hops:
            continue
        visited.add(name)
        step = recipe.steps.get(name)
        if step is None:
            continue
        if step.tool == "push_to_remote":
            return True
        # Expand successors
        successors: list[str] = []
        if step.on_success:
            successors.append(step.on_success)
        if step.on_failure:
            successors.append(step.on_failure)
        if step.on_result:
            for cond in step.on_result.conditions or []:
                if cond.route:
                    successors.append(cond.route)
        for s in successors:
            if s in recipe.steps:
                queue.append((s, hops + 1))
    return False


_CI_RECOVERY_STEP_NAMES = {"resolve_ci", "resolve_review"}


def _find_resolve_steps_reaching_push(recipe) -> list[tuple[str, object]]:
    """Return (step_name, step) pairs for CI-recovery skill steps that reach push_to_remote.

    Only checks steps named resolve_ci or resolve_review — the CI recovery path
    specifically identified as the deadlock source. Other resolve-failures steps
    (e.g. pre-merge worktree fixing) are intentionally excluded.
    """
    results = []
    for step_name, step in recipe.steps.items():
        if step_name not in _CI_RECOVERY_STEP_NAMES:
            continue
        if step.tool != "run_skill":
            continue
        cmd = step.with_args.get("skill_command", "")
        if not any(skill in cmd for skill in _CI_FIX_SKILLS):
            continue
        results.append((step_name, step))
    return results


@pytest.mark.parametrize("recipe_name", _PIPELINE_RECIPES)
def test_resolve_ci_steps_have_no_unconditional_on_success_to_push(
    recipe_name: str,
) -> None:
    """resolve-failures / resolve-review steps must NOT use on_success: re_push[_review]."""
    recipe_path = _RECIPES_DIR / recipe_name
    recipe = load_recipe(recipe_path)
    resolve_steps = _find_resolve_steps_reaching_push(recipe)
    assert resolve_steps, (
        f"{recipe_name}: expected to find at least one resolve-failures or resolve-review step"
    )
    violations = []
    for step_name, step in resolve_steps:
        if step.on_success is not None:
            violations.append(
                f"{step_name}: on_success={step.on_success!r} (must use on_result: instead)"
            )
    assert not violations, (
        f"{recipe_name} has resolve-ci/review steps with unconditional on_success: "
        f"These must route via on_result: with verdict dispatch to prevent the "
        f"unconditional re-push loop:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize("recipe_name", _PIPELINE_RECIPES)
def test_resolve_ci_steps_use_verdict_gated_on_result(recipe_name: str) -> None:
    """Every resolve-failures / resolve-review step must have verdict-gated on_result."""
    recipe_path = _RECIPES_DIR / recipe_name
    recipe = load_recipe(recipe_path)
    resolve_steps = _find_resolve_steps_reaching_push(recipe)
    assert resolve_steps, (
        f"{recipe_name}: expected to find at least one resolve-failures or resolve-review step"
    )
    not_gated = []
    for step_name, step in resolve_steps:
        if not _has_verdict_on_result(step):
            not_gated.append(step_name)
    assert not not_gated, (
        f"{recipe_name}: these resolve steps lack verdict-gated on_result: {not_gated}. "
        "Each step must include at least one condition matching "
        "'${{ result.verdict }} == ...'"
    )


@pytest.mark.parametrize("recipe_name", _PIPELINE_RECIPES)
def test_resolve_ci_on_result_routes_real_fix_to_re_push(recipe_name: str) -> None:
    """The verdict=real_fix condition must route to re_push (or re_push_review)."""
    recipe_path = _RECIPES_DIR / recipe_name
    recipe = load_recipe(recipe_path)
    resolve_steps = _find_resolve_steps_reaching_push(recipe)
    for step_name, step in resolve_steps:
        if step.on_result is None:
            continue
        real_fix_routes = [
            cond.route
            for cond in (step.on_result.conditions or [])
            if cond.when and "real_fix" in cond.when
        ]
        if not real_fix_routes:
            continue  # no explicit real_fix route yet — other tests catch this
        assert any("re_push" in r for r in real_fix_routes), (
            f"{recipe_name}/{step_name}: verdict=real_fix must route to a re_push step, "
            f"got routes: {real_fix_routes}"
        )


@pytest.mark.parametrize("recipe_name", _PIPELINE_RECIPES)
def test_resolve_ci_on_result_routes_escalation_verdicts_to_failure(
    recipe_name: str,
) -> None:
    """flake_suspected and ci_only_failure must route to release_issue_failure."""
    recipe_path = _RECIPES_DIR / recipe_name
    recipe = load_recipe(recipe_path)
    resolve_steps = _find_resolve_steps_reaching_push(recipe)
    for step_name, step in resolve_steps:
        if step.on_result is None:
            continue
        for verdict in ("flake_suspected", "ci_only_failure"):
            routes = [
                cond.route
                for cond in (step.on_result.conditions or [])
                if cond.when and verdict in cond.when
            ]
            if not routes:
                continue  # if not present yet, other tests catch missing verdict
            assert any("failure" in r or "escalat" in r for r in routes), (
                f"{recipe_name}/{step_name}: verdict={verdict} must route to "
                f"a failure/escalation step, got routes: {routes}"
            )


@pytest.mark.parametrize("recipe_name", _PIPELINE_RECIPES)
def test_pre_resolve_rebase_step_exists(recipe_name: str) -> None:
    """pre_resolve_rebase step must exist for already_green re-entry path."""
    recipe_path = _RECIPES_DIR / recipe_name
    recipe = load_recipe(recipe_path)
    assert "pre_resolve_rebase" in recipe.steps, (
        f"{recipe_name}: missing 'pre_resolve_rebase' step. "
        "This step is required for the already_green verdict re-entry path."
    )
