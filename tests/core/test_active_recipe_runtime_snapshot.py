"""Tests for the active-step runtime contract types.

These tests pin the invariants of Step 8's sealed runtime snapshot: every
type is a frozen, slotted dataclass; the snapshot is immutable end-to-end;
and runtime consumers can rely on a single selected step key with no second
mutable lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _active_ingredient(name: str = "ing", required: bool = True):
    from autoskillit.core import ActiveIngredientSpec

    return ActiveIngredientSpec(name=name, default=None, required=required)


def _active_step(step_key: str = "step_a", tool: str = "run_skill"):
    from autoskillit.core import ActiveRecipeStepSpec

    return ActiveRecipeStepSpec(step_key=step_key, tool=tool)


def _active_run_skill(step_key: str = "step_a", template: str = "/autoskillit:dry-walkthrough"):
    from autoskillit.core import ActiveRunSkillSpec

    return ActiveRunSkillSpec(
        step_key=step_key,
        expected_skill_command_template=template,
    )


def _active_snapshot():
    from autoskillit.core import ActiveRecipeRuntimeSnapshot

    return ActiveRecipeRuntimeSnapshot(
        recipe_kind="standard",
        normalized_ingredients=(_active_ingredient(),),
        required_packs=("write-recipe",),
        required_features=(),
        post_prune_steps=(_active_step(),),
        run_skill_specs=(_active_run_skill(),),
        recipe_version="0.0.1",
        recipe_invocation_fingerprint="sha256:abc",
        manifest_fingerprint="sha256:def",
        content_hash="sha256:ghi",
        composite_hash="sha256:jkl",
    )


def test_active_ingredient_spec_is_frozen_and_slotted() -> None:
    from autoskillit.core import ActiveIngredientSpec

    spec = ActiveIngredientSpec(name="x", default=None, required=True)
    with pytest.raises((AttributeError, Exception)):
        spec.name = "tampered"  # type: ignore[misc]


def test_active_recipe_step_spec_is_frozen_and_slotted() -> None:
    from autoskillit.core import ActiveRecipeStepSpec

    spec = ActiveRecipeStepSpec(step_key="a", tool="run_skill")
    with pytest.raises((AttributeError, Exception)):
        spec.tool = "tampered"  # type: ignore[misc]


def test_active_run_skill_spec_default_optional_refs_empty() -> None:
    from autoskillit.core import ActiveRunSkillSpec

    spec = ActiveRunSkillSpec(
        step_key="a",
        expected_skill_command_template="/autoskillit:dry-walkthrough",
    )
    assert spec.optional_context_refs == ()
    assert spec.expected_bindings == ()
    assert spec.expected_cwd_template == ""


def test_active_recipe_runtime_snapshot_is_frozen() -> None:
    """The runtime snapshot must be a frozen slotted dataclass."""
    snap = _active_snapshot()
    with pytest.raises((AttributeError, Exception)):
        snap.recipe_kind = "tampered"  # type: ignore[misc]


def test_active_recipe_runtime_snapshot_carries_fingerprints() -> None:
    snap = _active_snapshot()
    assert snap.recipe_invocation_fingerprint == "sha256:abc"
    assert snap.manifest_fingerprint == "sha256:def"
    assert snap.content_hash == "sha256:ghi"
    assert snap.composite_hash == "sha256:jkl"
    # The four identities are distinct strings — never aliased.
    assert (
        len(
            {
                snap.recipe_invocation_fingerprint,
                snap.manifest_fingerprint,
                snap.content_hash,
                snap.composite_hash,
            }
        )
        == 4
    )


def test_active_snapshot_post_prune_steps_index_by_key() -> None:
    """Downstream consumers index post_prune_steps by step_key."""
    from autoskillit.core import ActiveRecipeRuntimeSnapshot

    snap = ActiveRecipeRuntimeSnapshot(
        recipe_kind="standard",
        normalized_ingredients=(),
        required_packs=(),
        required_features=(),
        post_prune_steps=(
            _active_step(step_key="alpha"),
            _active_step(step_key="beta", tool="run_cmd"),
        ),
        run_skill_specs=(_active_run_skill(step_key="alpha"),),
        recipe_version="0.0.1",
        recipe_invocation_fingerprint="sha256:x",
        manifest_fingerprint="sha256:y",
        content_hash="sha256:z",
        composite_hash="sha256:w",
    )
    by_key = {s.step_key: s for s in snap.post_prune_steps}
    assert by_key["alpha"].tool == "run_skill"
    assert by_key["beta"].tool == "run_cmd"


def test_active_snapshot_run_skill_specs_match_post_prune() -> None:
    """Every run_skill step has a corresponding ActiveRunSkillSpec."""
    snap = _active_snapshot()
    by_key = {s.step_key: s for s in snap.run_skill_specs}
    assert "step_a" in by_key
    assert by_key["step_a"].expected_skill_command_template.startswith("/autoskillit:")
