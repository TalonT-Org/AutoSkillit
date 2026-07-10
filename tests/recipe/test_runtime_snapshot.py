"""Tests for construction of the sealed active-recipe runtime snapshot."""

from __future__ import annotations

import pytest

from autoskillit.recipe import build_active_recipe_runtime_snapshot
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _recipe() -> Recipe:
    return Recipe(
        name="demo",
        description="demo",
        recipe_version="1.2.3",
        content_hash="sha256:content",
        composite_hash="sha256:composite",
        requires_packs=["github"],
        requires_features=["providers"],
        ingredients={
            "issue_url": RecipeIngredient(description="issue", required=True),
        },
        steps={
            "run": RecipeStep(
                name="run",
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:dry-walkthrough "
                        "${{ context.plan_path }} ${{ inputs.issue_url }} -"
                    ),
                    "cwd": "${{ context.work_dir }}",
                    "output_dir": ".",
                },
                provider="minimax",
                on_success="done",
            ),
            "pruned": RecipeStep(name="pruned", tool="run_cmd", with_args={"cmd": "true"}),
            "done": RecipeStep(name="done", action="stop"),
        },
    )


def test_runtime_snapshot_seals_post_prune_steps_and_run_skill_shape() -> None:
    snapshot = build_active_recipe_runtime_snapshot(
        _recipe(),
        post_prune_step_names=["run", "done"],
        project_identity="project-id",
    )
    assert [step.step_key for step in snapshot.post_prune_steps] == ["run", "done"]
    assert [step.step_key for step in snapshot.run_skill_specs] == ["run"]
    run_spec = snapshot.run_skill_specs[0]
    assert run_spec.declared_step_provider == "minimax"
    assert run_spec.declared_output_dir == "."
    assert [binding.name for binding in run_spec.expected_bindings] == [
        "plan_path",
        "issue_url",
        "remediation_path",
    ]
    assert run_spec.expected_bindings[1].ref_namespace == "inputs"
    assert run_spec.expected_bindings[1].ref_name == "issue_url"


def test_runtime_snapshot_keeps_identity_fields_distinct() -> None:
    snapshot = build_active_recipe_runtime_snapshot(
        _recipe(), post_prune_step_names=["run", "done"]
    )
    assert snapshot.recipe_kind == "demo"
    assert snapshot.recipe_version == "1.2.3"
    assert snapshot.content_hash == "sha256:content"
    assert snapshot.composite_hash == "sha256:composite"
    assert snapshot.manifest_fingerprint.startswith("sha256:")
    assert snapshot.recipe_invocation_fingerprint.startswith("sha256:")
    assert len(
        {
            snapshot.content_hash,
            snapshot.composite_hash,
            snapshot.manifest_fingerprint,
            snapshot.recipe_invocation_fingerprint,
        }
    ) == 4
