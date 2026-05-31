"""MCP tool: record_pipeline_step — pipeline step tracker init and status."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import DISPATCH_ID_ENV_VAR, atomic_write, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import _hook_config_overlay_path, _pipeline_tracker_path
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


def _resolve_skipped_steps(overlay_path: Path, pipeline_id: str) -> set[str]:
    if not overlay_path.exists():
        return set()
    try:
        overlay = json.loads(overlay_path.read_text())
        pid_locks = overlay.get("locked_steps", {}).get(pipeline_id, {})
        return {s for s, v in pid_locks.items() if v is False}
    except (json.JSONDecodeError, OSError):
        return set()


def _build_tracker_steps(
    active_steps: dict[str, object], skipped: set[str]
) -> dict[str, dict[str, str]]:
    return {
        name: {"status": "skipped"} if name in skipped else {"status": "pending"}
        for name in active_steps
    }


def _compute_status_counts(
    steps: dict[str, dict[str, object]], dependencies: dict[str, list[str]]
) -> dict[str, int]:
    return {
        "complete": sum(1 for s in steps.values() if s.get("status") == "complete"),
        "pending": sum(1 for s in steps.values() if s.get("status") == "pending"),
        "skipped": sum(1 for s in steps.values() if s.get("status") == "skipped"),
        "blocked": sum(
            1
            for sname, sdata in steps.items()
            if sdata.get("status") == "pending" and sname in dependencies
        ),
    }


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

    Requires the kitchen to be open. Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    try:
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
            return _handle_init(ctx, tracker_path, effective_pipeline_id, dependencies)

        if op == "status":
            return _handle_status(tracker_path, effective_pipeline_id)

        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": f"record_pipeline_step: unknown op '{op}'. Use 'init' or 'status'.",
            }
        )
    except Exception:
        logger.exception("record_pipeline_step_unexpected_error")
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": "record_pipeline_step: unexpected internal error.",
            }
        )


def _handle_init(
    ctx: object,
    tracker_path: Path,
    effective_pipeline_id: str,
    dependencies: dict[str, list[str]] | None,
) -> str:
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

    active_steps = ctx.active_recipe_steps  # type: ignore[attr-defined]
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

    overlay_path = _hook_config_overlay_path(ctx.project_dir)  # type: ignore[attr-defined]
    skipped = _resolve_skipped_steps(overlay_path, effective_pipeline_id)
    steps = _build_tracker_steps(active_steps, skipped)

    tracker_data = {
        "pipeline_id": effective_pipeline_id,
        "kitchen_id": ctx.kitchen_id,  # type: ignore[attr-defined]
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


def _handle_status(tracker_path: Path, effective_pipeline_id: str) -> str:
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
    deps = tracker_data.get("dependencies", {})
    counts = _compute_status_counts(steps, deps)

    return json.dumps(
        {
            "success": True,
            "pipeline_id": effective_pipeline_id,
            "steps": steps,
            "dependencies": deps,
            **counts,
            "total": len(steps),
        }
    )
