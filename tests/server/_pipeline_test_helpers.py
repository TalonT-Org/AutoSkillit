"""Shared pipeline-tracker test helpers for tests/server/ and tests/integration/."""

from __future__ import annotations

import json


def _write_tracker(tmp_path, pipeline_id, steps, dependencies, kitchen_id="test-kitchen"):
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_dir.joinpath(f"{pipeline_id}.json").write_text(
        json.dumps(
            {
                "pipeline_id": pipeline_id,
                "kitchen_id": kitchen_id,
                "initialized_at": "2026-05-31T01:00:00Z",
                "steps": steps,
                "dependencies": dependencies,
            }
        )
    )


def _setup_project(tmp_path, tool_ctx_kitchen_open, active_recipe_steps=None):
    from autoskillit.recipe.schema import RecipeStep

    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / ".hook_config.json").write_text("{}")
    tool_ctx_kitchen_open.project_dir = tmp_path
    if active_recipe_steps is None:
        active_recipe_steps = {
            "rectify": RecipeStep(name="rectify"),
            "review_approach": RecipeStep(name="review_approach"),
        }
    tool_ctx_kitchen_open.active_recipe_steps = active_recipe_steps
