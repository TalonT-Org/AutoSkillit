"""Admission and exact-delivery boundary for ``run_skill`` receipts."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
from fastmcp import Context, FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import TextContent

from autoskillit.core import RunSkillCompletionAuthority, get_logger
from autoskillit.pipeline import RunSkillCompletionReceipt

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext

logger = get_logger(__name__)

__all__ = [
    "FinalizedRunSkillCompletionResponse",
    "RunSkillCompletionMiddleware",
    "_request_session_identity",
    "current_request_session_id",
    "stage_run_skill_completion_response",
]


@dataclass(frozen=True, slots=True)
class FinalizedRunSkillCompletionResponse:
    """Rendered response whose draft receipt awaits exact wire preservation."""

    rendered: str
    authority: RunSkillCompletionAuthority
    receipt: RunSkillCompletionReceipt


_request_session_id: ContextVar[str] = ContextVar("run_skill_request_session_id", default="")
_staged_response: ContextVar[FinalizedRunSkillCompletionResponse | None] = ContextVar(
    "run_skill_staged_response", default=None
)


def current_request_session_id() -> str:
    """Return the trusted FastMCP session identity captured by middleware."""
    return _request_session_id.get()


def _request_session_identity(request_context: Context) -> str:
    """Resolve the current request session, including direct-call fallbacks."""
    request_session_id = current_request_session_id()
    if not request_session_id:
        try:
            request_session_id = str(request_context.session_id or "")
        except (AttributeError, RuntimeError):
            request_session_id = ""
    if not request_session_id:
        request_session_id = f"direct:{id(request_context)}"
    return request_session_id


def stage_run_skill_completion_response(
    finalized: FinalizedRunSkillCompletionResponse,
) -> FinalizedRunSkillCompletionResponse:
    """Bind finalized receipt metadata to the current request."""
    _staged_response.set(finalized)
    return finalized


def _exact_receipt(result: ToolResult, receipt_id: str) -> bool:
    if len(result.content) != 1 or not isinstance(result.content[0], TextContent):
        return False
    text = result.content[0].text
    if result.structured_content != {"result": text}:
        return False
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(parsed, dict) and parsed.get("receipt_id") == receipt_id


def _compact_response(finalized: FinalizedRunSkillCompletionResponse) -> str:
    receipt = finalized.receipt
    payload: dict[str, object] = {
        "success": receipt.success,
        "receipt_id": receipt.receipt_id,
        "classification": receipt.classification,
        "subtype": receipt.classification,
        "is_error": not receipt.success,
        "exit_code": 0 if receipt.success else -1,
        "needs_retry": not receipt.success,
        "retry_reason": "none" if receipt.success else receipt.classification,
        "result_digest": receipt.result_digest,
        "result": finalized.rendered,
    }
    if not receipt.success:
        payload["error"] = "run_skill completed without success"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class RunSkillCompletionMiddleware(Middleware):
    """Enforce completion admission and publish only exact delivered receipts."""

    def __init__(self, mcp: FastMCP) -> None:
        self._mcp = mcp

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        from autoskillit.server._state import (  # circular-break
            _get_ctx_or_none,
        )

        fastmcp_context = getattr(context, "fastmcp_context", None)
        session_id = str(getattr(fastmcp_context, "session_id", "") or "")
        session_token = _request_session_id.set(session_id)
        staged_token = _staged_response.set(None)
        try:
            registered_tool = await self._mcp.get_tool(context.message.name)
            tool_ctx = _get_ctx_or_none()
            authority = tool_ctx.run_skill_completion if tool_ctx is not None else None
            if authority is not None:
                allowed, reason = authority.admission(context.message.name)
                if not allowed and isinstance(registered_tool, FunctionTool):
                    denial = json.dumps(
                        {
                            "success": False,
                            "is_error": True,
                            "error": "run_skill_completion_pending",
                            "stage": "preflight:run_skill_completion",
                            "retriable": False,
                            "user_visible_message": reason,
                        },
                        separators=(",", ":"),
                    )
                    return registered_tool.convert_result(denial)

            try:
                result = await call_next(context)
                finalized = _staged_response.get()
                if finalized is None:
                    return result
                if not _exact_receipt(result, finalized.receipt.receipt_id):
                    if not isinstance(registered_tool, FunctionTool):
                        logger.error("run_skill_receipt_unrepresentable")
                        return result
                    result = registered_tool.convert_result(_compact_response(finalized))
                if not _exact_receipt(result, finalized.receipt.receipt_id):
                    logger.error(
                        "run_skill_receipt_not_preserved",
                        receipt_id=finalized.receipt.receipt_id,
                    )
                    return result
                with anyio.CancelScope(shield=True):
                    finalized.authority.publish(finalized.receipt.receipt_id)
                return result
            except BaseException:
                finalized = _staged_response.get()
                if finalized is not None:
                    finalized.authority.discard_draft(finalized.receipt.receipt_id)
                raise
        finally:
            _staged_response.reset(staged_token)
            _request_session_id.reset(session_token)
