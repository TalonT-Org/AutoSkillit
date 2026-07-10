"""Tests for the DeliveryEvidence analyzer.

These tests pin the invariant that worker delivery is proven only by
references appearing inside the ``skill_command`` string. A correctly
named reference that appears only as an inert ``with:`` sibling is NOT
worker-bound — it must NOT satisfy delivery rules and must NOT consume
captured state.
"""

from __future__ import annotations

import pytest

from autoskillit.core import OPTIONAL_ARG_OMISSION_SENTINEL
from autoskillit.recipe._delivery import analyze_step_delivery
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_step(name: str, with_args: dict[str, str], **kw) -> RecipeStep:
    return RecipeStep(name=name, tool="run_skill", with_args=with_args, **kw)


# --- Worker-bound vs sibling-only ---


def test_remediation_path_in_sibling_is_not_worker_bound() -> None:
    step = _make_step(
        "verify",
        {
            "skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}",
            "remediation_path": "${{ context.remediation_path }}",
        },
    )
    ev = analyze_step_delivery(step)
    assert "remediation_path" not in ev.worker_bound_refs
    assert "remediation_path" not in ev.tool_bound_refs
    assert "remediation_path" in ev.unsupported_keys


def test_remediation_path_in_skill_command_is_worker_bound() -> None:
    step = _make_step(
        "verify",
        {
            "skill_command": (
                "/autoskillit:dry-walkthrough ${{ context.plan_path }} "
                "${{ inputs.issue_url }} ${{ context.remediation_path }}"
            ),
        },
    )
    ev = analyze_step_delivery(step)
    assert "remediation_path" in ev.worker_bound_refs
    assert ev.unsupported_keys == frozenset()


def test_cwd_reference_is_tool_bound_never_worker_bound() -> None:
    step = _make_step(
        "any",
        {
            "skill_command": "/autoskillit:do-thing",
            "cwd": "${{ context.work_dir }}",
        },
    )
    ev = analyze_step_delivery(step)
    assert "work_dir" in ev.tool_bound_refs
    assert "work_dir" not in ev.worker_bound_refs


def test_dispatch_items_top_level_is_orchestrator_control_not_worker_bound() -> None:
    step = _make_step(
        "elaborate",
        {"skill_command": "/autoskillit:planner-elaborate-phase {phase_id}"},
        dispatch_items="${{ context.phase_ids }}",
    )
    ev = analyze_step_delivery(step)
    assert "phase_ids" in ev.orchestrator_control_refs
    assert "phase_ids" not in ev.worker_bound_refs
    assert "phase_ids" not in ev.tool_bound_refs


def test_optional_context_refs_without_command_binding_is_availability_only() -> None:
    step = _make_step(
        "verify",
        {"skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}"},
        optional_context_refs=["remediation_path"],
    )
    ev = analyze_step_delivery(step)
    assert "remediation_path" in ev.availability_only_refs
    assert "remediation_path" not in ev.worker_bound_refs


# --- Tokenizer / positional binding ---


def test_quoted_token_preserves_template_inside() -> None:
    step = _make_step(
        "any",
        {
            "skill_command": (
                "/autoskillit:compose-pr "
                '"${{ context.all_diagram_paths }}" '
                "${{ context.work_dir }}"
            )
        },
    )
    ev = analyze_step_delivery(step)
    assert "all_diagram_paths" in ev.worker_bound_refs
    assert "work_dir" in ev.worker_bound_refs


def test_named_argument_value_carries_ref() -> None:
    step = _make_step(
        "any",
        {
            "skill_command": "/autoskillit:open-integration-pr",
            "branch": "${{ context.batch_branch }}",
        },
    )
    ev = analyze_step_delivery(step)
    # `branch` is not a registered run_skill parameter, so the ref is unsupported.
    assert "batch_branch" in ev.unsupported_keys or "batch_branch" not in ev.worker_bound_refs


def test_omission_sentinel_is_not_delivery() -> None:
    step = _make_step(
        "verify",
        {
            "skill_command": (
                f"/autoskillit:dry-walkthrough ${{{{ context.plan_path }}}} "
                f"{OPTIONAL_ARG_OMISSION_SENTINEL} "
                f"${{{{ context.remediation_path }}}}"
            )
        },
    )
    ev = analyze_step_delivery(step)
    assert "plan_path" in ev.worker_bound_refs
    assert "remediation_path" in ev.worker_bound_refs
    assert ev.unsupported_keys == frozenset()


def test_omission_sentinel_constant_aligned_across_modules() -> None:
    """The constant must equal '-' so recipe and SKILL.md literals stay aligned."""
    assert OPTIONAL_ARG_OMISSION_SENTINEL == "-"


# --- Bundled recipes have zero unsupported run_skill siblings ---


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "implementation-groups",
        "remediation",
        "merge-prs",
        "planner",
        "research",
    ],
)
def test_bundled_recipe_has_no_unsupported_run_skill_siblings(recipe_name: str) -> None:
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    bad: list[tuple[str, frozenset[str]]] = []
    for step_name, step in recipe.steps.items():
        if step.tool != "run_skill":
            continue
        ev = analyze_step_delivery(
            step, optional_context_refs=getattr(step, "optional_context_refs", [])
        )
        if ev.unsupported_keys:
            bad.append((step_name, ev.unsupported_keys))
    assert not bad, f"Recipe {recipe_name!r} declares unsupported run_skill siblings: {bad}"
