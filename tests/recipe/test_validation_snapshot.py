"""Tests for the ValidationSnapshot and the validation context wiring.

These tests pin the invariant that one validation pass produces one
immutable snapshot: a deeply-immutable owned recipe view, a normalized
manifest, delivery evidence keyed by the snapshot's fingerprint pair, and
identical fingerprint strings on cache hits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(name: str = "test_recipe") -> Recipe:
    """Minimal Recipe stub for snapshot construction."""
    from autoskillit.recipe.schema import Recipe, RecipeStep

    return Recipe(
        name=name,
        description="test recipe",
        version="0.0.1",
        kitchen_rules=[],
        steps={
            "step_a": RecipeStep(
                name="step_a",
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:dry-walkthrough ${{ context.plan_path }}"
                },
            ),
        },
    )


def test_validation_snapshot_is_frozen_slotted() -> None:
    """ValidationSnapshot must be a frozen slotted dataclass."""
    from types import MappingProxyType

    from autoskillit.recipe._analysis import ValidationSnapshot
    from autoskillit.recipe._delivery import DeliveryEvidenceMap
    from autoskillit.recipe.schema import DataFlowReport

    snap = ValidationSnapshot(
        declared_recipe=MappingProxyType({}),
        effective_recipe=MappingProxyType({}),
        declared_evidence=DeliveryEvidenceMap(steps={}, manifest_snapshot_id="x"),
        effective_evidence=DeliveryEvidenceMap(steps={}, manifest_snapshot_id="x"),
        declared_graph=MappingProxyType({}),
        effective_graph=MappingProxyType({}),
        declared_dataflow=DataFlowReport(),
        effective_dataflow=DataFlowReport(),
        declared_blocks=(),
        effective_blocks=(),
        normalized_manifest=MappingProxyType({}),
        manifest_fingerprint="sha256:abc",
        recipe_invocation_fingerprint="sha256:def",
    )
    # frozen: cannot mutate fields
    with pytest.raises((AttributeError, Exception)):
        snap.manifest_fingerprint = "tampered"  # type: ignore[misc]


def test_build_validation_snapshot_owns_recipe_copy() -> None:
    """Mutating the original recipe must not affect the snapshot."""
    from autoskillit.recipe._analysis import build_validation_snapshot

    recipe = _make_recipe()
    snap = build_validation_snapshot(recipe)
    original_command = recipe.steps["step_a"].with_args["skill_command"]
    # Mutate the original — must not be visible through the snapshot.
    recipe.steps["step_a"].with_args["skill_command"] = "/tampered"
    # Snapshot's owned view is a deep copy — RecipeStep values are dict-coerced
    # through vars() inside build_validation_snapshot. Reach in via vars to
    # compare against the original skill_command.
    snap_steps = dict(snap.owned_recipe["steps"])  # type: ignore[index]
    snap_step_a = vars(snap_steps["step_a"])
    assert snap_step_a["with_args"]["skill_command"] == original_command
    # Restore for any downstream tests.
    recipe.steps["step_a"].with_args["skill_command"] = original_command


def test_build_validation_snapshot_fingerprints_are_stable() -> None:
    """Two snapshots of the same recipe produce the same fingerprints."""
    from autoskillit.recipe._analysis import build_validation_snapshot

    snap1 = build_validation_snapshot(_make_recipe("stable"))
    snap2 = build_validation_snapshot(_make_recipe("stable"))
    assert snap1.manifest_fingerprint == snap2.manifest_fingerprint
    assert snap1.recipe_invocation_fingerprint == snap2.recipe_invocation_fingerprint


def test_build_validation_snapshot_fingerprints_change_with_invocations() -> None:
    """Changing step structure changes the invocation fingerprint."""
    from autoskillit.recipe._analysis import build_validation_snapshot
    from autoskillit.recipe.schema import Recipe, RecipeStep

    base = _make_recipe("a")
    different = Recipe(
        name="a",
        description="test recipe",
        version="0.0.1",
        kitchen_rules=[],
        steps={
            "step_a": RecipeStep(
                name="step_a",
                tool="run_skill",
                with_args={"skill_command": "/a/b"},
            ),
            "step_b": RecipeStep(
                name="step_b",
                tool="run_cmd",
                with_args={"cmd": "ls"},
            ),
        },
    )
    snap_base = build_validation_snapshot(base)
    snap_diff = build_validation_snapshot(different)
    assert snap_base.recipe_invocation_fingerprint != snap_diff.recipe_invocation_fingerprint


def test_invocation_fingerprint_changes_for_same_key_command_change() -> None:
    from autoskillit.recipe._analysis import build_validation_snapshot

    first = _make_recipe("same")
    second = _make_recipe("same")
    second.steps["step_a"].with_args["skill_command"] = (
        "/autoskillit:dry-walkthrough ${{ inputs.other_path }}"
    )
    assert (
        build_validation_snapshot(first).recipe_invocation_fingerprint
        != build_validation_snapshot(second).recipe_invocation_fingerprint
    )


def test_invocation_fingerprint_changes_for_same_key_tool_change() -> None:
    from autoskillit.recipe._analysis import build_validation_snapshot

    first = _make_recipe("same")
    second = _make_recipe("same")
    second.steps["step_a"].tool = "run_cmd"
    assert (
        build_validation_snapshot(first).recipe_invocation_fingerprint
        != build_validation_snapshot(second).recipe_invocation_fingerprint
    )


def test_make_validation_context_carries_snapshot() -> None:
    """ValidationContext now carries contract_snapshot + delivery evidence + fingerprints."""
    from autoskillit.recipe._analysis import make_validation_context

    ctx = make_validation_context(_make_recipe("with_ctx"))
    assert ctx.contract_snapshot is not None
    assert ctx.delivery_evidence is not None
    assert ctx.manifest_fingerprint != ""
    assert ctx.recipe_invocation_fingerprint != ""
    # The snapshot's evidence must equal the context's evidence field.
    assert ctx.delivery_evidence is ctx.contract_snapshot.delivery_evidence


def test_make_validation_context_reuses_supplied_snapshot() -> None:
    """A pre-built snapshot is reused rather than rebuilt."""
    from autoskillit.recipe._analysis import build_validation_snapshot, make_validation_context

    snap = build_validation_snapshot(_make_recipe("reused"))
    ctx = make_validation_context(_make_recipe("reused"), contract_snapshot=snap)
    assert ctx.contract_snapshot is snap
    assert ctx.manifest_fingerprint == snap.manifest_fingerprint
    assert ctx.recipe_invocation_fingerprint == snap.recipe_invocation_fingerprint


def test_validation_snapshot_owned_recipe_is_immutable_view() -> None:
    """MappingProxyType prevents mutation through the snapshot view."""
    from autoskillit.recipe._analysis import build_validation_snapshot

    snap = build_validation_snapshot(_make_recipe("immutable"))
    with pytest.raises(TypeError):
        snap.owned_recipe["steps"] = {}  # type: ignore[index]
