"""Server-owned MCP boundary for review-audit ingestion and finalization."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

import autoskillit.smoke_utils.review as _review_audit
from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server._notify import track_response_size
from autoskillit.server.lifecycle._session_scope import SCOPE_ANY, session_scoped
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
@track_response_size("plan_review_audit")
async def plan_review_audit(
    authority_path: str,
    review_output_dir: str,
    deletion_merge_base: str = "",
    anchor_authority_path: str = "",
    repository: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Plan immutable auditor slots from the retained review authority. Never raises."""
    try:
        del ctx
        result = _review_audit.plan_review_audit(
            authority_path=authority_path,
            review_output_dir=review_output_dir,
            deletion_merge_base=deletion_merge_base,
            anchor_authority_path=anchor_authority_path,
            repository=repository,
        )
        return json.dumps({"success": True, **result})
    except Exception as exc:
        logger.error("plan_review_audit failed", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
@track_response_size("collect_review_audit")
async def collect_review_audit(
    manifest_path: str,
    handles: dict[str, str],
    ctx: Context = CurrentContext(),
) -> str:
    """Collect bounded auditor results through the active backend locator. Never raises."""
    try:
        del ctx
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        if tool_ctx.backend is None:
            return json.dumps({"success": False, "error": "backend_unavailable"})
        locator = tool_ctx.backend.session_locator()
        result = _review_audit.collect_review_audit(
            manifest_path=manifest_path,
            handles=handles,
            read_child_task=locator.read_child_task,
        )
        return json.dumps({"success": True, **result})
    except Exception as exc:
        logger.error("collect_review_audit failed", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@session_scoped(SCOPE_ANY)
@_cancellation_shield()
@track_response_size("finalize_review_audit")
async def finalize_review_audit(
    manifest_path: str,
    handles: dict[str, str],
    dispositions: list[dict[str, Any]],
    prior_resolved_findings: list[dict[str, Any]],
    final_snapshot_state: str,
    ctx: Context = CurrentContext(),
) -> str:
    """Revalidate auditors and derive the final review verdict. Never raises."""
    try:
        del ctx
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        if tool_ctx.backend is None:
            return json.dumps({"success": False, "error": "backend_unavailable"})
        locator = tool_ctx.backend.session_locator()
        result = _review_audit.finalize_review_audit(
            manifest_path=manifest_path,
            handles=handles,
            dispositions=dispositions,
            prior_resolved_findings=prior_resolved_findings,
            final_snapshot_state=final_snapshot_state,
            read_child_task=locator.read_child_task,
        )
        return json.dumps({"success": True, **result})
    except Exception as exc:
        logger.error("finalize_review_audit failed", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
