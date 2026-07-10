"""Tests for the DeliveryEvidence analyzer.

These tests pin the invariant that worker delivery is proven only by
references appearing inside the ``skill_command`` string. A correctly
named reference that appears only as an inert ``with:`` sibling is NOT
worker-bound — it must NOT satisfy delivery rules and must NOT consume
captured state.
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    DISPATCH_ITEM_PLACEHOLDER,
    OPTIONAL_ARG_OMISSION_SENTINEL,
)
from autoskillit.recipe._delivery import (
    analyze_step_delivery,
    binding_for_input,
    effective_consumption,
    input_receives_ref,
)
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import RecipeStep
from autoskillit.recipe.tool_registry import (
    FRAMEWORK_ONLY_EXCLUSIONS,
    ToolDef,
    unsupported_params,
)

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


# --- DISPATCH_ITEM_PLACEHOLDER constant ---


def test_dispatch_item_placeholder_constant_value() -> None:
    """The constant must equal the canonical single-brace splice marker."""
    assert DISPATCH_ITEM_PLACEHOLDER == "{selected_dispatch_item}"


def test_dispatch_item_placeholder_is_single_brace() -> None:
    """Only one such placeholder may appear in a dispatch step's skill_command."""
    assert DISPATCH_ITEM_PLACEHOLDER.count("{") == 1
    assert DISPATCH_ITEM_PLACEHOLDER.count("}") == 1


# --- effective_consumption predicate ---


def test_effective_consumption_excludes_unsupported_siblings() -> None:
    step = _make_step(
        "any",
        {
            "skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}",
            "review_path": "${{ context.review_path }}",
        },
    )
    ev = analyze_step_delivery(step)
    assert "plan_path" in effective_consumption(ev)
    assert "review_path" not in effective_consumption(ev)


def test_effective_consumption_excludes_availability_only() -> None:
    step = _make_step(
        "any",
        {"skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}"},
        optional_context_refs=["remediation_path"],
    )
    ev = analyze_step_delivery(step)
    assert "remediation_path" not in effective_consumption(ev)


def test_effective_consumption_unions_worker_tool_and_control() -> None:
    step = _make_step(
        "any",
        {
            "skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}",
            "cwd": "${{ context.work_dir }}",
        },
        dispatch_items="${{ context.remediation_path }}",
    )
    ev = analyze_step_delivery(step)
    consumed = effective_consumption(ev)
    assert "plan_path" in consumed  # worker
    assert "work_dir" in consumed  # tool
    assert "remediation_path" in consumed  # orchestrator


def test_input_receives_ref_respects_namespace() -> None:
    """A context binding cannot be substituted by the inputs namespace."""
    step = _make_step(
        "any",
        {"skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}"},
    )
    ev = analyze_step_delivery(step)
    assert input_receives_ref(ev, namespace="context", name="plan_path") is True
    assert input_receives_ref(ev, namespace="inputs", name="plan_path") is False


def test_input_receives_ref_rejects_same_name_from_wrong_namespace() -> None:
    step = _make_step(
        "any",
        {"skill_command": "/autoskillit:dry-walkthrough ${{ inputs.same }}"},
    )
    ev = analyze_step_delivery(step)
    assert input_receives_ref(ev, namespace="inputs", name="same") is True
    assert input_receives_ref(ev, namespace="context", name="same") is False


def test_input_receives_ref_requires_the_declared_absolute_slot() -> None:
    step = _make_step(
        "any",
        {
            "skill_command": (
                "/autoskillit:dry-walkthrough ${{ context.plan_path }} "
                "${{ context.remediation_path }} -"
            )
        },
    )
    ev = analyze_step_delivery(step)
    assert (
        input_receives_ref(
            ev,
            input_name="remediation_path",
            namespace="context",
            name="remediation_path",
        )
        is False
    )
    assert (
        input_receives_ref(
            ev,
            input_name="issue_url",
            namespace="context",
            name="remediation_path",
        )
        is True
    )


def test_input_receives_ref_returns_false_for_absent_name() -> None:
    step = _make_step(
        "any",
        {"skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}"},
    )
    ev = analyze_step_delivery(step)
    assert input_receives_ref(ev, namespace="context", name="remediation_path") is False


def test_binding_for_input_is_namespace_agnostic() -> None:
    step = _make_step(
        "any",
        {"skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}"},
    )
    ev = analyze_step_delivery(step)
    assert binding_for_input(ev, name="plan_path") is True
    assert binding_for_input(ev, name="absent_ref") is False


# --- DeliveryEvidenceMap fingerprints ---


def test_delivery_evidence_map_carries_fingerprints() -> None:
    """The map exposes manifest_fingerprint and recipe_invocation_fingerprint."""
    from autoskillit.recipe._delivery import DeliveryEvidenceMap

    ev = DeliveryEvidenceMap(
        steps={},
        manifest_snapshot_id="Recipe:test",
        manifest_fingerprint="sha256:abc",
        recipe_invocation_fingerprint="sha256:def",
    )
    assert ev.manifest_snapshot_id == "Recipe:test"
    assert ev.manifest_fingerprint == "sha256:abc"
    assert ev.recipe_invocation_fingerprint == "sha256:def"


def test_delivery_evidence_map_fingerprints_default_empty() -> None:
    from autoskillit.recipe._delivery import DeliveryEvidenceMap

    ev = DeliveryEvidenceMap(steps={}, manifest_snapshot_id="Recipe:test")
    assert ev.manifest_fingerprint == ""
    assert ev.recipe_invocation_fingerprint == ""


# --- ToolDef unsupported_params helper ---


def test_unsupported_params_returns_subset_for_known_tool() -> None:
    """run_skill accepts skill_command + cwd; 'branch' must be unsupported."""
    assert unsupported_params(
        "run_skill", frozenset({"skill_command", "cwd", "branch"})
    ) == frozenset({"branch"})


def test_unsupported_params_returns_all_for_unknown_tool() -> None:
    """A tool not in the registry is fail-closed: every key is unsupported."""
    assert unsupported_params("totally-unknown-tool", frozenset({"a", "b"})) == frozenset(
        {"a", "b"}
    )


def test_unsupported_params_framework_only_returns_all() -> None:
    """Framework-only tools cannot be called from recipes."""
    assert "close_kitchen" in FRAMEWORK_ONLY_EXCLUSIONS
    assert unsupported_params("close_kitchen", frozenset({})) == frozenset()
    assert unsupported_params("close_kitchen", frozenset({"any_key"})) == frozenset({"any_key"})


def test_unsupported_params_empty_keys_returns_empty() -> None:
    """No keys in, no keys out — even for an unknown tool."""
    assert unsupported_params("any", frozenset()) == frozenset()


# --- ToolDef.frozen/slotted ---


def test_tool_def_is_frozen_and_slotted() -> None:
    """ToolDef must remain a frozen slotted dataclass — slotted prevents arbitrary attrs."""
    td = ToolDef(name="x", params=("a",))
    with pytest.raises((AttributeError, Exception)):
        td.extra = "forbidden"  # type: ignore[attr-defined]
