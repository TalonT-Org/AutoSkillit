"""MCP tools for recovering and acknowledging delivered ``run_skill`` results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled, _require_orchestrator_exact
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._types import deny_envelope
from autoskillit.server.tools.tools_execution import _request_session_identity

logger = get_logger(__name__)


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("recover_run_skill_result")
async def recover_run_skill_result(
    ctx: Context = CurrentContext(),
) -> str:
    """Recover the sole delivered ``run_skill`` receipt after transport loss. Never raises."""
    if (tier_gate := _require_orchestrator_exact("recover_run_skill_result")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        authority = tool_ctx.run_skill_completion
        if authority is None:
            raise RuntimeError("run_skill completion authority is unavailable")
        receipt = authority.recover(
            kitchen_id=tool_ctx.kitchen_id,
            request_session_id=_request_session_identity(ctx),
        )
        return json.dumps(
            {
                "success": True,
                "receipt_id": receipt.receipt_id,
                "run_skill_success": receipt.success,
                "classification": receipt.classification,
                "session_id": receipt.child_session_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except ValueError as exc:
        return json.dumps(
            deny_envelope(
                f"recover_run_skill_result: {exc}",
                stage="preflight:run_skill_completion",
                retriable=False,
            )
        )
    except Exception:
        logger.exception("recover_run_skill_result_unexpected_error")
        return json.dumps(
            deny_envelope(
                "recover_run_skill_result: unexpected internal error.",
                stage="complete:run_skill_completion",
                retriable=True,
            )
        )


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("complete_run_skill_result")
async def complete_run_skill_result(
    receipt_id: str,
    ctx: Context = CurrentContext(),
) -> str:
    """Acknowledge one exactly delivered ``run_skill`` result. Never raises."""
    if (tier_gate := _require_orchestrator_exact("complete_run_skill_result")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx  # circular-break
        from autoskillit.server.tools.tools_pipeline_tracker import (  # circular-break
            mark_step_complete,
        )

        tool_ctx = _get_ctx()
        authority = tool_ctx.run_skill_completion
        if authority is None:
            raise RuntimeError("run_skill completion authority is unavailable")
        receipt = authority.acknowledge(
            receipt_id,
            kitchen_id=tool_ctx.kitchen_id,
            request_session_id=_request_session_identity(ctx),
        )
        tracker_result: Mapping[str, object] | None = None
        if receipt.success and receipt.tracker_incarnation_id:
            tracker_result = authority.apply_tracker_credit(
                tracker_order_id=receipt.tracker_order_id,
                tracker_path=receipt.tracker_path,
                tracker_kitchen_id=receipt.tracker_kitchen_id,
                tracker_incarnation_id=receipt.tracker_incarnation_id,
                step_name=receipt.step_name,
                receipt_id=receipt.receipt_id,
                effect=lambda: mark_step_complete(
                    Path(receipt.tracker_path),
                    receipt.step_name,
                    receipt.tracker_order_id,
                    expected_tracker_kitchen_id=receipt.tracker_kitchen_id,
                    expected_tracker_incarnation_id=receipt.tracker_incarnation_id,
                ),
            )
        return json.dumps(
            {
                "success": True,
                "receipt_id": receipt.receipt_id,
                "run_skill_success": receipt.success,
                "classification": receipt.classification,
                "session_id": receipt.child_session_id,
                "tracker": tracker_result,
                "tracker_repairable": bool(
                    receipt.success
                    and receipt.tracker_incarnation_id
                    and not (tracker_result or {}).get("success")
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except ValueError as exc:
        return json.dumps(
            deny_envelope(
                f"complete_run_skill_result: {exc}",
                stage="preflight:run_skill_completion",
                retriable=False,
            )
        )
    except Exception:
        logger.exception("complete_run_skill_result_unexpected_error")
        return json.dumps(
            deny_envelope(
                "complete_run_skill_result: unexpected internal error.",
                stage="complete:run_skill_completion",
                retriable=True,
            )
        )
