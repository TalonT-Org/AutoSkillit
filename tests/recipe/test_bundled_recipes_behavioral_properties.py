"""Behavioral property registry tests for bundled recipes.

These tests assert that bundled recipes satisfy structural behavioral
properties (on_context_limit coverage, dispatch mode consistency,
model adequacy for context-intensive steps) beyond simple schema
presence. They serve as a second line of defense alongside the
semantic rules in recipe/rules/ — if a rule's severity is reduced
or a finding is suppressed, these tests still catch the gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import all_validated_recipe_paths, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALL_PATHS = all_validated_recipe_paths(_PROJECT_ROOT)
_BUNDLED_ONLY = [p for p in _ALL_PATHS if "src/autoskillit/recipes" in str(p)]
assert _BUNDLED_ONLY, "no bundled recipes found"
_RECIPE_NAMES = [p.name for p in _BUNDLED_ONLY]


CONTEXT_LIMIT_EXEMPT_STEPS: dict[str, set[str]] = {
    "planner": set(),
    "remediation": set(),
    "research": set(),
    "implementation": set(),
    "implementation-groups": set(),
    "merge-prs": set(),
    "full-audit": set(),
    "bem-wrapper": set(),
    "research-design": set(),
    "research-implement": set(),
    "research-review": set(),
}

PARALLEL_ELIGIBLE_DISPATCH_STEPS: dict[str, set[str]] = {
    "planner": {
        "elaborate_phases",
        "elaborate_assignments",
        "elaborate_wps",
        "refine_assignments",
        "refine_wps",
    },
}

CONTEXT_INTENSIVE_STEPS: dict[str, set[str]] = {
    "planner": {"elaborate_wps", "elaborate_assignments", "elaborate_phases"},
}


def _recipe_base_name(filename: str) -> str:
    return filename.removesuffix(".yaml")


_CONTEXT_LIMIT_COMPLIANT_RECIPES: set[str] = {
    "consolidate-health-reports",
    "implement-findings",
    "planner",
    "promote-to-main",
    "promote-to-main-wrapper",
    "research-archive",
    "research-campaign",
}


@pytest.mark.parametrize("recipe_name", _RECIPE_NAMES)
def test_run_skill_steps_declare_on_context_limit(recipe_name: str) -> None:
    """Every run_skill step must declare on_context_limit (or be exempt)."""
    if _recipe_base_name(recipe_name) not in _CONTEXT_LIMIT_COMPLIANT_RECIPES:
        pytest.xfail("on_context_limit handlers not yet added to all bundled recipes")
    recipe_path = next(p for p in _BUNDLED_ONLY if p.name == recipe_name)
    recipe = load_recipe(recipe_path)
    exempt = CONTEXT_LIMIT_EXEMPT_STEPS.get(_recipe_base_name(recipe_name), set())

    context_limit_targets: set[str] = set()
    for step in recipe.steps.values():
        if step.on_context_limit and step.on_context_limit not in (
            "escalate",
            "release_issue_failure",
        ):
            context_limit_targets.add(step.on_context_limit)

    missing: list[str] = []
    for name, step in recipe.steps.items():
        if step.tool != "run_skill":
            continue
        if step.action == "stop":
            continue
        if step.on_context_limit is not None:
            continue
        if name in context_limit_targets:
            continue
        if name in exempt:
            continue
        missing.append(name)

    assert not missing, (
        f"{recipe_name}: run_skill steps missing on_context_limit: {missing}. "
        f"Add on_context_limit: <recovery_step> to each, or add to CONTEXT_LIMIT_EXEMPT_STEPS."
    )


@pytest.mark.parametrize(
    "recipe_name",
    [n for n in _RECIPE_NAMES if _recipe_base_name(n) in PARALLEL_ELIGIBLE_DISPATCH_STEPS],
)
def test_parallel_eligible_steps_use_parallel_dispatch(recipe_name: str) -> None:
    """Steps listed as parallel-eligible must use PARALLEL in step.note."""
    recipe_path = next(p for p in _BUNDLED_ONLY if p.name == recipe_name)
    recipe = load_recipe(recipe_path)
    base = _recipe_base_name(recipe_name)
    eligible = PARALLEL_ELIGIBLE_DISPATCH_STEPS.get(base, set())

    for step_name in eligible:
        step = recipe.steps[step_name]
        assert step.note, f"{recipe_name}.{step_name}: must have a note for dispatch instructions"
        assert "parallel" in step.note.lower(), (
            f"{recipe_name}.{step_name}: note must mention parallel dispatch. Got: {step.note!r}"
        )
        assert "sequential" not in step.note.lower(), (
            f"{recipe_name}.{step_name}: note must not mention sequential dispatch. "
            f"Got: {step.note!r}"
        )


@pytest.mark.parametrize(
    "recipe_name",
    [n for n in _RECIPE_NAMES if _recipe_base_name(n) in CONTEXT_INTENSIVE_STEPS],
)
def test_context_intensive_steps_declare_explicit_model(recipe_name: str) -> None:
    """Context-intensive steps must declare model != '' (not rely on default fallthrough)."""
    recipe_path = next(p for p in _BUNDLED_ONLY if p.name == recipe_name)
    recipe = load_recipe(recipe_path)
    base = _recipe_base_name(recipe_name)
    intensive = CONTEXT_INTENSIVE_STEPS.get(base, set())

    for step_name in intensive:
        step = recipe.steps[step_name]
        assert step.model is not None and step.model != "", (
            f"{recipe_name}.{step_name}: context-intensive step must declare an explicit "
            f"model (not empty string), got model={step.model!r}"
        )
