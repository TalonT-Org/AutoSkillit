"""MCP tool: record_pipeline_step — pipeline step tracker init and status."""

from __future__ import annotations

import json
import os
from datetime import UTC

from autoskillit.core import DISPATCH_ID_ENV_VAR, atomic_write, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import _hook_config_overlay_path, _pipeline_tracker_path
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("record_pipeline_step")
async def record_pipeline_step(
    pipeline_id: str = "",
    op: str = "status",
    dependencies: dict[str, list[str]] | None = None,
) -> str:
    """Initialize or query the pipeline step completion tracker.

    **op="init"**: Creates the tracker file with the server-authoritative step list
    from the currently open recipe. The LLM provides the dependency graph at init time.
    Idempotent — calling init twice with the same pipeline_id returns an error.

    **op="status"**: Returns the current state of all tracked steps.

    Requires the kitchen to be open.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    effective_pipeline_id = pipeline_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    if not effective_pipeline_id:
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": (
                    "record_pipeline_step: pipeline_id is required. "
                    "Pass pipeline_id explicitly or set "
                    "AUTOSKILLIT_DISPATCH_ID in the environment."
                ),
            }
        )

    from autoskillit.server import _get_ctx

    ctx = _get_ctx()
    tracker_path = _pipeline_tracker_path(ctx.project_dir, effective_pipeline_id)

    if op == "init":
        if tracker_path.exists():
            return json.dumps(
                {
                    "success": False,
                    "is_error": True,
                    "error": (
                        f"record_pipeline_step: pipeline '{effective_pipeline_id}' "
                        "has already been initialized."
                    ),
                }
            )

        active_steps = ctx.active_recipe_steps
        if active_steps is None:
            return json.dumps(
                {
                    "success": False,
                    "is_error": True,
                    "error": (
                        "record_pipeline_step: no active recipe steps found. "
                        "Ensure the kitchen is open with a loaded recipe."
                    ),
                }
            )

        overlay_path = _hook_config_overlay_path(ctx.project_dir)
        skipped_steps: set[str] = set()
        if overlay_path.exists():
            try:
                overlay = json.loads(overlay_path.read_text())
                locked_steps = overlay.get("locked_steps", {})
                pid_locks = locked_steps.get(effective_pipeline_id, {})
                skipped_steps = {s for s, v in pid_locks.items() if v is False}
            except (json.JSONDecodeError, OSError):
                pass

        steps: dict[str, dict] = {}
        for step_name in active_steps:
            if step_name in skipped_steps:
                steps[step_name] = {"status": "skipped"}
            else:
                steps[step_name] = {"status": "pending"}

        from datetime import datetime

        tracker_data = {
            "pipeline_id": effective_pipeline_id,
            "kitchen_id": ctx.kitchen_id,
            "initialized_at": datetime.now(UTC).isoformat(),
            "steps": steps,
            "dependencies": dependencies or {},
        }

        tracker_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(tracker_path, json.dumps(tracker_data))

        return json.dumps(
            {
                "success": True,
                "pipeline_id": effective_pipeline_id,
                "step_count": len(steps),
                "dependency_count": len(dependencies or {}),
            }
        )

    if op == "status":
        if not tracker_path.exists():
            return json.dumps(
                {
                    "success": False,
                    "is_error": True,
                    "error": (
                        f"record_pipeline_step: no tracker found for pipeline "
                        f"'{effective_pipeline_id}'. Initialize with op='init' first."
                    ),
                }
            )

        try:
            tracker_data = json.loads(tracker_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            return json.dumps(
                {
                    "success": False,
                    "is_error": True,
                    "error": f"record_pipeline_step: failed to read tracker: {exc}",
                }
            )

        steps = tracker_data.get("steps", {})
        complete = sum(1 for s in steps.values() if s.get("status") == "complete")
        pending = sum(1 for s in steps.values() if s.get("status") == "pending")
        skipped = sum(1 for s in steps.values() if s.get("status") == "skipped")
        blocked = sum(
            1
            for sname, sdata in steps.items()
            if sdata.get("status") == "pending" and sname in tracker_data.get("dependencies", {})
        )

        return json.dumps(
            {
                "success": True,
                "pipeline_id": effective_pipeline_id,
                "steps": steps,
                "dependencies": tracker_data.get("dependencies", {}),
                "complete": complete,
                "pending": pending,
                "skipped": skipped,
                "blocked": blocked,
                "total": len(steps),
            }
        )

    return json.dumps(
        {
            "success": False,
            "is_error": True,
            "error": f"record_pipeline_step: unknown op '{op}'. Use 'init' or 'status'.",
        }
    )
