"""Exact-response invariants for the run_skill completion boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import CallToolRequestParams, ImageContent, TextContent

from autoskillit.pipeline import DefaultRunSkillCompletionAuthority
from autoskillit.server._run_skill_completion import (
    FinalizedRunSkillCompletionResponse,
    RunSkillCompletionMiddleware,
    _compact_response,
    _exact_receipt,
    current_request_session_id,
    stage_run_skill_completion_response,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _finalized(
    authority: DefaultRunSkillCompletionAuthority | None = None,
    *,
    success: bool = True,
) -> FinalizedRunSkillCompletionResponse:
    authority = authority or DefaultRunSkillCompletionAuthority()
    invocation = authority.begin(
        kitchen_id="kitchen",
        request_session_id="request",
        tracker_order_id="order",
        tracker_path="/tracker.json",
        tracker_kitchen_id="kitchen",
        tracker_incarnation_id="incarnation",
        step_name="investigate",
    )
    receipt = authority.draft(
        invocation,
        classification="success" if success else "timeout",
        success=success,
        result_digest="sha256:digest",
    )
    rendered = json.dumps(
        {
            "success": success,
            "result": "investigation complete",
            "receipt_id": receipt.receipt_id,
        }
    )
    return FinalizedRunSkillCompletionResponse(rendered, authority, receipt)


def _tool_result(text: str, *, structured: object | None = None) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"result": text} if structured is None else structured,
    )


def _context() -> MiddlewareContext[CallToolRequestParams]:
    return MiddlewareContext(
        message=CallToolRequestParams(name="kitchen_status", arguments={}),
        fastmcp_context=SimpleNamespace(session_id="request-session"),  # type: ignore[arg-type]
        method="tools/call",
        type="request",
    )


def _result_text(result: ToolResult) -> str:
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    return result.content[0].text


def _registered_tool(name: str = "kitchen_status") -> FunctionTool:
    async def placeholder() -> str:
        return "unused"

    return FunctionTool.from_function(placeholder, name=name)


def test_exact_receipt_requires_single_conformant_text_and_exact_json_field() -> None:
    finalized = _finalized()
    receipt_id = finalized.receipt.receipt_id

    assert _exact_receipt(_tool_result(finalized.rendered), receipt_id)
    assert not _exact_receipt(
        _tool_result(json.dumps({"result": f"mentions {receipt_id}"})), receipt_id
    )
    assert not _exact_receipt(
        _tool_result(finalized.rendered, structured={"result": "rewritten"}), receipt_id
    )


@pytest.mark.parametrize(
    "case",
    [
        "empty_content",
        "multiple_content",
        "non_text",
        "invalid_json",
        "json_list",
        "wrong_receipt",
    ],
)
def test_exact_receipt_rejects_malformed_delivery_shapes(case: str) -> None:
    receipt_id = "receipt"
    if case == "empty_content":
        result = ToolResult(content=[], structured_content={})
    elif case == "multiple_content":
        rendered = json.dumps({"receipt_id": receipt_id})
        text = TextContent(type="text", text=rendered)
        result = ToolResult(content=[text, text], structured_content={"result": rendered})
    elif case == "non_text":
        image = ImageContent(type="image", data="", mimeType="image/png")
        result = ToolResult(content=[image], structured_content={})
    elif case == "invalid_json":
        result = _tool_result("not json")
    elif case == "json_list":
        result = _tool_result("[]")
    else:
        result = _tool_result(json.dumps({"receipt_id": "other"}))

    assert not _exact_receipt(result, receipt_id)


def test_compact_fallback_preserves_authoritative_projection_and_full_result() -> None:
    finalized = _finalized()

    compact = json.loads(_compact_response(finalized))

    assert compact["receipt_id"] == finalized.receipt.receipt_id
    assert compact["classification"] == "success"
    assert compact["success"] is True
    assert compact["needs_retry"] is False
    assert compact["result_digest"] == "sha256:digest"
    assert compact["result"] == finalized.rendered


def test_compact_fallback_preserves_failure_projection() -> None:
    finalized = _finalized(success=False)

    compact = json.loads(_compact_response(finalized))

    assert compact["success"] is False
    assert compact["is_error"] is True
    assert compact["exit_code"] == -1
    assert compact["needs_retry"] is True
    assert compact["retry_reason"] == "timeout"
    assert compact["error"] == "run_skill completed without success"
    assert compact["result"] == finalized.rendered


@pytest.mark.anyio
async def test_middleware_denies_other_tools_while_receipt_is_pending(monkeypatch) -> None:
    authority = _finalized().authority
    monkeypatch.setattr(
        "autoskillit.server._state._get_ctx_or_none",
        lambda: SimpleNamespace(run_skill_completion=authority),
    )
    call_next = AsyncMock()
    registered = _registered_tool()
    fake_mcp = SimpleNamespace(get_tool=AsyncMock(return_value=registered))

    result = await RunSkillCompletionMiddleware(fake_mcp).on_call_tool(  # type: ignore[arg-type]
        _context(), call_next
    )

    denial = json.loads(_result_text(result))
    assert denial["error"] == "run_skill_completion_pending"
    assert denial["stage"] == "preflight:run_skill_completion"
    assert denial["retriable"] is False
    assert denial["user_visible_message"]
    call_next.assert_not_awaited()
    assert current_request_session_id() == ""


@pytest.mark.anyio
@pytest.mark.parametrize("exact_delivery", [True, False])
async def test_middleware_publishes_exact_and_compacted_deliveries(
    monkeypatch, exact_delivery: bool
) -> None:
    authority = DefaultRunSkillCompletionAuthority()
    monkeypatch.setattr(
        "autoskillit.server._state._get_ctx_or_none",
        lambda: SimpleNamespace(run_skill_completion=authority),
    )
    registered = _registered_tool()
    fake_mcp = SimpleNamespace(get_tool=AsyncMock(return_value=registered))
    finalized: FinalizedRunSkillCompletionResponse | None = None

    async def call_next(_context):
        nonlocal finalized
        assert current_request_session_id() == "request-session"
        finalized = stage_run_skill_completion_response(_finalized(authority))
        delivered = finalized.rendered if exact_delivery else "rewritten"
        return registered.convert_result(delivered)

    result = await RunSkillCompletionMiddleware(fake_mcp).on_call_tool(  # type: ignore[arg-type]
        _context(), call_next
    )

    assert finalized is not None
    assert json.loads(_result_text(result))["receipt_id"] == finalized.receipt.receipt_id
    acknowledged = authority.acknowledge(
        finalized.receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="request",
    )
    assert acknowledged.receipt_id == finalized.receipt.receipt_id
    assert current_request_session_id() == ""


@pytest.mark.anyio
async def test_middleware_discards_unrepresentable_delivery(monkeypatch) -> None:
    authority = DefaultRunSkillCompletionAuthority()
    monkeypatch.setattr(
        "autoskillit.server._state._get_ctx_or_none",
        lambda: SimpleNamespace(run_skill_completion=authority),
    )
    fake_mcp = SimpleNamespace(get_tool=AsyncMock(return_value=object()))
    finalized: FinalizedRunSkillCompletionResponse | None = None
    original = _tool_result("rewritten")

    async def call_next(_context):
        nonlocal finalized
        finalized = stage_run_skill_completion_response(_finalized(authority))
        return original

    middleware = RunSkillCompletionMiddleware(fake_mcp)  # type: ignore[arg-type]
    result = await middleware.on_call_tool(_context(), call_next)

    assert result is original
    assert finalized is not None
    with pytest.raises(ValueError, match="unknown or already-published"):
        authority.publish(finalized.receipt.receipt_id)
    assert authority.admission("kitchen_status") == (True, "idle")
    assert current_request_session_id() == ""


@pytest.mark.anyio
async def test_middleware_discards_staged_draft_on_base_exception(monkeypatch) -> None:
    authority = DefaultRunSkillCompletionAuthority()
    monkeypatch.setattr(
        "autoskillit.server._state._get_ctx_or_none",
        lambda: SimpleNamespace(run_skill_completion=authority),
    )
    registered = _registered_tool()
    fake_mcp = SimpleNamespace(get_tool=AsyncMock(return_value=registered))

    class Sentinel(BaseException):
        pass

    sentinel = Sentinel()
    staged: FinalizedRunSkillCompletionResponse | None = None

    async def call_next(_context):
        nonlocal staged
        staged = stage_run_skill_completion_response(_finalized(authority))
        raise sentinel

    with pytest.raises(Sentinel) as raised:
        await RunSkillCompletionMiddleware(fake_mcp).on_call_tool(  # type: ignore[arg-type]
            _context(), call_next
        )

    assert raised.value is sentinel
    assert staged is not None
    with pytest.raises(ValueError, match="unknown or already-published"):
        authority.publish(staged.receipt.receipt_id)
    assert authority.admission("kitchen_status") == (True, "idle")
    assert current_request_session_id() == ""


@pytest.mark.anyio
async def test_middleware_preserves_original_exception_when_discard_fails(monkeypatch) -> None:
    class FailingDiscardAuthority(DefaultRunSkillCompletionAuthority):
        def discard_draft(self, receipt_id: str) -> bool:
            raise RuntimeError(f"cannot discard {receipt_id}")

    authority = FailingDiscardAuthority()
    monkeypatch.setattr(
        "autoskillit.server._state._get_ctx_or_none",
        lambda: SimpleNamespace(run_skill_completion=authority),
    )
    fake_mcp = SimpleNamespace(get_tool=AsyncMock(return_value=_registered_tool()))

    class Sentinel(BaseException):
        pass

    sentinel = Sentinel()

    async def call_next(_context):
        stage_run_skill_completion_response(_finalized(authority))
        raise sentinel

    with pytest.raises(Sentinel) as raised:
        await RunSkillCompletionMiddleware(fake_mcp).on_call_tool(  # type: ignore[arg-type]
            _context(), call_next
        )

    assert raised.value is sentinel
    assert current_request_session_id() == ""
