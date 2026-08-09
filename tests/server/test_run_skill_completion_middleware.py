"""Exact-response invariants for the run_skill completion boundary."""

from __future__ import annotations

import json

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from autoskillit.pipeline import DefaultRunSkillCompletionAuthority
from autoskillit.server._run_skill_completion import (
    FinalizedRunSkillCompletionResponse,
    _compact_response,
    _exact_receipt,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _finalized() -> FinalizedRunSkillCompletionResponse:
    authority = DefaultRunSkillCompletionAuthority()
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
        classification="success",
        success=True,
        result_digest="sha256:digest",
    )
    rendered = json.dumps(
        {"success": True, "result": "investigation complete", "receipt_id": receipt.receipt_id}
    )
    return FinalizedRunSkillCompletionResponse(rendered, authority, receipt)


def _tool_result(text: str, *, structured: object | None = None) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"result": text} if structured is None else structured,
    )


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


def test_compact_fallback_preserves_authoritative_projection_and_full_result() -> None:
    finalized = _finalized()

    compact = json.loads(_compact_response(finalized))

    assert compact["receipt_id"] == finalized.receipt.receipt_id
    assert compact["classification"] == "success"
    assert compact["success"] is True
    assert compact["needs_retry"] is False
    assert compact["result_digest"] == "sha256:digest"
    assert compact["result"] == finalized.rendered
