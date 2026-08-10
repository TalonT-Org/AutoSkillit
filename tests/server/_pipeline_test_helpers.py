"""Shared pipeline-tracker test helpers for tests/server/ and tests/integration/."""

from __future__ import annotations

import json
from pathlib import Path


def _write_tracker(tmp_path, pipeline_id, steps, dependencies, kitchen_id="test-kitchen"):
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_dir.joinpath(f"{pipeline_id}.json").write_text(
        json.dumps(
            {
                "pipeline_id": pipeline_id,
                "kitchen_id": kitchen_id,
                "initialized_at": "2026-05-31T01:00:00Z",
                "tracker_incarnation_id": "test-incarnation",
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


def _ack_direct_run_skill_result(tool_ctx, payload):
    """Simulate the outer delivery boundary for a directly called handler."""
    from autoskillit.server.tools.tools_pipeline_tracker import mark_step_complete

    authority = tool_ctx.run_skill_completion
    assert authority is not None
    receipt = authority.publish(payload["receipt_id"])
    acknowledged = authority.acknowledge(
        receipt.receipt_id,
        kitchen_id=receipt.kitchen_id,
        request_session_id=receipt.request_session_id,
    )
    if not acknowledged.success or not acknowledged.tracker_incarnation_id:
        return None
    return authority.apply_tracker_credit(
        tracker_order_id=acknowledged.tracker_order_id,
        tracker_path=acknowledged.tracker_path,
        tracker_kitchen_id=acknowledged.tracker_kitchen_id,
        tracker_incarnation_id=acknowledged.tracker_incarnation_id,
        step_name=acknowledged.step_name,
        receipt_id=acknowledged.receipt_id,
        effect=lambda: mark_step_complete(
            Path(acknowledged.tracker_path),
            acknowledged.step_name,
            acknowledged.tracker_order_id,
            expected_tracker_kitchen_id=acknowledged.tracker_kitchen_id,
            expected_tracker_incarnation_id=acknowledged.tracker_incarnation_id,
        ),
    )
