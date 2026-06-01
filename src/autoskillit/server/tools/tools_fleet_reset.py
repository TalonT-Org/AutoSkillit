"""MCP tool handler: reset_dispatch."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import FleetErrorCode, IssueLabelState, fleet_error, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled, _require_fleet
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)

_VALID_RESET_TARGETS: dict[str, IssueLabelState] = {
    "queued": IssueLabelState.QUEUED,
    "fail": IssueLabelState.FAIL,
}


@mcp.tool(
    tags={"autoskillit", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield(result_type="fleet_error")
@track_response_size("reset_dispatch")
async def reset_dispatch(
    dispatch_id: str,
    reset_to: str = "queued",
    ctx: Context = CurrentContext(),
) -> str:
    """Reset a failed L2 dispatch, cleaning up all git/PR artifacts.

    Removes the local worktree, local and remote branches, closes any open PRs,
    and resets issue labels. Use after a resume attempt fails or is declined.

    Args:
        dispatch_id: The dispatch ID (UUID) or dispatch name to reset.
        reset_to: Target state — "queued" (fresh retry) or "fail" (abandon).
    """
    if (gate := _require_enabled()) is not None:
        return gate

    fleet_err = _require_fleet("reset_dispatch")
    if fleet_err is not None:
        return fleet_err

    if reset_to not in _VALID_RESET_TARGETS:
        return fleet_error(
            FleetErrorCode.FLEET_RESET_INVALID_TARGET,
            f"Invalid reset_to value: {reset_to!r}. Must be 'queued' or 'fail'.",
        )

    from autoskillit.fleet._label_cleanup import discover_campaign_state_files
    from autoskillit.fleet._reset import (
        _RESETTABLE_STATUSES,
        find_dispatch_in_campaigns,
        reset_dispatch_artifacts,
        update_campaign_state,
    )
    from autoskillit.fleet.state_types import DispatchStatus
    from autoskillit.server import _get_ctx
    from autoskillit.workspace.worktree import WORKTREES_DIR

    tool_ctx = _get_ctx()
    project_dir = tool_ctx.project_dir
    campaign_state_paths = discover_campaign_state_files(project_dir)

    result = find_dispatch_in_campaigns(dispatch_id, campaign_state_paths)
    if result is None:
        return fleet_error(
            FleetErrorCode.FLEET_RESET_NOT_FOUND,
            f"No dispatch found matching {dispatch_id!r} in any campaign state file.",
        )

    dispatch, state_path = result

    if dispatch.status == DispatchStatus.RUNNING:
        return fleet_error(
            FleetErrorCode.FLEET_RESET_STILL_RUNNING,
            f"Dispatch {dispatch_id!r} is still RUNNING. Wait for it to finish or reap it first.",
        )

    if dispatch.status not in _RESETTABLE_STATUSES:
        return fleet_error(
            FleetErrorCode.FLEET_RESET_NOT_FOUND,
            f"Dispatch {dispatch_id!r} is in {dispatch.status} state "
            f"(expected one of {sorted(s.value for s in _RESETTABLE_STATUSES)}).",
        )

    cfg = tool_ctx.config
    worktrees_dir = (
        Path(cfg.workspace.worktree_root)
        if cfg.workspace.worktree_root
        else project_dir.parent / WORKTREES_DIR
    )

    target_state = _VALID_RESET_TARGETS[reset_to]

    if tool_ctx.runner is None:
        return fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            "No subprocess runner available in this session.",
        )

    report = await reset_dispatch_artifacts(
        dispatch,
        project_dir=project_dir,
        worktrees_dir=worktrees_dir,
        runner=tool_ctx.runner,
        github_client=tool_ctx.github_client,
        target_state=target_state,
    )

    state_updated = await update_campaign_state(
        dispatch.name,
        state_path,
        reset_to_queued=(reset_to == "queued"),
    )
    report.state_updated = state_updated

    return json.dumps(
        {
            "success": True,
            "dispatch_name": report.dispatch_name,
            "branch_name": report.branch_name,
            "labels_reset": report.labels_reset,
            "worktree_removed": report.worktree_removed,
            "sidecar_removed": report.sidecar_removed,
            "local_branch_deleted": report.local_branch_deleted,
            "remote_branch_deleted": report.remote_branch_deleted,
            "prs_closed": report.prs_closed,
            "state_updated": report.state_updated,
            "errors": report.errors,
            "reset_to": reset_to,
        }
    )
