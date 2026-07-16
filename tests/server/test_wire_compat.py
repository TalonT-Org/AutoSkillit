from __future__ import annotations

import hashlib
import json
import typing
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import CallToolRequestParams, TextContent

from autoskillit.server._response_conformance import (
    _SCHEMA_NONCONFORMING_FAILURE,
    _WRAPPED_STRING_OUTPUT_SCHEMA,
    ResponseConformanceDecision,
    ResponseConformanceMiddleware,
    _converted_result_conforms,
    decide_response_conformance,
)
from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestClaudeCodeCompatMiddlewareEdgeCases:
    """Edge-case coverage for ClaudeCodeCompatMiddleware.on_list_tools."""

    @pytest.mark.anyio
    async def test_empty_tool_list(self):
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware

        mw = ClaudeCodeCompatMiddleware()
        ctx = MagicMock()
        call_next = AsyncMock(return_value=[])

        result = await mw.on_list_tools(ctx, call_next)

        assert result == []

    @pytest.mark.anyio
    async def test_title_stripped_when_output_schema_already_none(self):
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware

        mw = ClaudeCodeCompatMiddleware()
        tool = MagicMock()
        tool.name = "titled_tool"
        tool.title = "My Tool"
        tool.output_schema = None
        tool.model_copy.return_value = MagicMock(
            name="titled_tool",
            output_schema=None,
            title=None,
        )

        ctx = MagicMock()
        call_next = AsyncMock(return_value=[tool])

        result = await mw.on_list_tools(ctx, call_next)

        assert result[0].title is None
        tool.model_copy.assert_called_once_with(
            update={"output_schema": None, "title": None},
        )

    @pytest.mark.anyio
    async def test_model_copy_called_unconditionally_when_fields_already_none(self):
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware

        mw = ClaudeCodeCompatMiddleware()
        tool = MagicMock()
        tool.name = "clean_tool"
        tool.output_schema = None
        tool.title = None
        tool.model_copy.return_value = MagicMock(
            name="clean_tool",
            output_schema=None,
            title=None,
        )

        ctx = MagicMock()
        call_next = AsyncMock(return_value=[tool])

        result = await mw.on_list_tools(ctx, call_next)

        tool.model_copy.assert_called_once_with(
            update={"output_schema": None, "title": None},
        )
        assert result[0] is tool.model_copy.return_value

    @pytest.mark.anyio
    async def test_mixed_tool_list_all_cleaned(self):
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware

        mw = ClaudeCodeCompatMiddleware()
        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_a.output_schema = {"type": "string"}
        tool_a.title = "Tool A"
        tool_a.model_copy.return_value = MagicMock(
            name="tool_a",
            output_schema=None,
            title=None,
        )

        tool_b = MagicMock()
        tool_b.name = "tool_b"
        tool_b.output_schema = None
        tool_b.title = None
        tool_b.model_copy.return_value = MagicMock(
            name="tool_b",
            output_schema=None,
            title=None,
        )

        ctx = MagicMock()
        call_next = AsyncMock(return_value=[tool_a, tool_b])

        result = await mw.on_list_tools(ctx, call_next)

        assert len(result) == 2
        assert result[0].output_schema is None
        assert result[0].title is None
        assert result[1].output_schema is None
        assert result[1].title is None


class TestClaudeCodeCompatMiddlewareDispatchChain:
    """Dispatch-chain coverage: __call__ → _dispatch_handler → on_list_tools.

    Unlike TestClaudeCodeCompatMiddlewareEdgeCases (which calls on_list_tools
    directly), these tests drive the middleware through Middleware.__call__ so
    that _dispatch_handler routing is exercised. A FastMCP dispatch change that
    breaks method→hook routing will be caught here.
    """

    @pytest.mark.anyio
    async def test_tools_list_dispatches_through_chain(self):
        mw = ClaudeCodeCompatMiddleware()

        tool = MagicMock()
        tool.name = "chain_tool"
        tool.output_schema = {"type": "string"}
        tool.title = "Chain Tool"
        tool.model_copy.return_value = MagicMock(
            name="chain_tool",
            output_schema=None,
            title=None,
        )

        ctx = MiddlewareContext(message=MagicMock(), method="tools/list", type="request")

        async def call_next(context):
            return [tool]

        result = await mw(ctx, call_next)

        tool.model_copy.assert_called_once_with(
            update={"output_schema": None, "title": None},
        )
        assert result[0].output_schema is None
        assert result[0].title is None

    @pytest.mark.anyio
    async def test_non_tools_list_method_is_passthrough(self):
        mw = ClaudeCodeCompatMiddleware()

        sentinel = object()
        ctx = MiddlewareContext(message=MagicMock(), method="resources/list", type="request")

        async def call_next(context):
            return sentinel

        result = await mw(ctx, call_next)
        assert result is sentinel

    @pytest.mark.anyio
    async def test_dispatch_chain_preserves_annotations(self):
        mw = ClaudeCodeCompatMiddleware()

        tool = MagicMock()
        tool.name = "annotated_tool"
        tool.output_schema = {"type": "string"}
        tool.title = "Annotated"
        tool.annotations = MagicMock(readOnlyHint=True)
        copy = MagicMock()
        copy.name = "annotated_tool"
        copy.output_schema = None
        copy.title = None
        copy.annotations = MagicMock(readOnlyHint=True)
        tool.model_copy.return_value = copy

        ctx = MiddlewareContext(message=MagicMock(), method="tools/list", type="request")

        async def call_next(context):
            return [tool]

        result = await mw(ctx, call_next)
        assert result[0].annotations is not None
        assert result[0].annotations.readOnlyHint is True


class TestResponseConformanceDecisionTable:
    @pytest.mark.parametrize(
        ("inputs", "expected"),
        [
            (
                (False, True, True, True),
                ResponseConformanceDecision.BYPASS_NON_FUNCTION_TOOL,
            ),
            (
                (True, False, True, True),
                ResponseConformanceDecision.BYPASS_HANDLER_TYPE,
            ),
            (
                (True, True, False, True),
                ResponseConformanceDecision.BYPASS_REGISTERED_SCHEMA,
            ),
            (
                (True, True, True, True),
                ResponseConformanceDecision.CONFORMING,
            ),
            (
                (True, True, True, False),
                ResponseConformanceDecision.REWRITE_NONCONFORMING,
            ),
        ],
    )
    def test_decision_table(self, inputs, expected):
        assert (
            decide_response_conformance(
                is_function_tool=inputs[0],
                handler_returns_exact_str=inputs[1],
                registered_schema_is_wrapped_string=inputs[2],
                converted_result_conforms=inputs[3],
            )
            is expected
        )


class TestResponseConformanceMiddleware:
    @pytest.mark.anyio
    async def test_registered_advertised_and_handler_contracts(self, kitchen_enabled):
        from autoskillit.server import mcp

        registered = await mcp.get_tool("kitchen_status")
        assert isinstance(registered, FunctionTool)
        assert registered.output_schema == _WRAPPED_STRING_OUTPUT_SCHEMA
        assert typing.get_type_hints(registered.fn)["return"] is str

        async with Client(mcp) as client:
            advertised = {tool.name: tool for tool in await client.list_tools()}

        assert advertised["kitchen_status"].outputSchema is None

    @pytest.mark.anyio
    async def test_all_registered_string_tools_are_conforming_decision_entries(self):
        from autoskillit.core import ALL_VISIBILITY_TAGS
        from autoskillit.server import mcp

        mcp._transforms.clear()
        try:
            mcp.enable(tags=set(ALL_VISIBILITY_TAGS))
            visible_tools = await mcp.list_tools()
            assert visible_tools

            for visible_tool in visible_tools:
                registered = await mcp.get_tool(visible_tool.name)
                assert isinstance(registered, FunctionTool), visible_tool.name
                handler_returns_str = typing.get_type_hints(registered.fn).get("return") is str
                schema_is_wrapped = registered.output_schema == _WRAPPED_STRING_OUTPUT_SCHEMA
                converted = registered.convert_result("registry-sweep")
                converted_conforms = isinstance(
                    registered.output_schema, dict
                ) and _converted_result_conforms(converted, registered.output_schema)
                assert (
                    decide_response_conformance(
                        is_function_tool=True,
                        handler_returns_exact_str=handler_returns_str,
                        registered_schema_is_wrapped_string=schema_is_wrapped,
                        converted_result_conforms=converted_conforms,
                    )
                    is ResponseConformanceDecision.CONFORMING
                ), visible_tool.name
        finally:
            mcp._transforms.clear()

    @pytest.mark.anyio
    async def test_middleware_is_appended_once_after_wire_compat(self):
        from autoskillit.server import mcp

        names = [type(middleware).__name__ for middleware in mcp.middleware]
        assert names.count("ResponseConformanceMiddleware") == 1
        assert names.index("ResponseConformanceMiddleware") == (
            names.index("ClaudeCodeCompatMiddleware") + 1
        )

    @pytest.mark.anyio
    async def test_valid_converted_string_result_passes_through(
        self, kitchen_enabled, monkeypatch
    ):
        from autoskillit.server import mcp

        payload = '{"success":true,"kind":"spill_or_exemption"}'
        original_run = FunctionTool.run

        async def run(tool, arguments):
            if tool.name == "kitchen_status":
                return tool.convert_result(payload)
            return await original_run(tool, arguments)

        monkeypatch.setattr(FunctionTool, "run", run)
        async with Client(mcp) as client:
            result = await client.call_tool("kitchen_status", {})

        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == payload
        assert result.structured_content == {"result": payload}

    @pytest.mark.anyio
    async def test_nonconforming_conversion_is_replaced_without_original(
        self, kitchen_enabled, monkeypatch
    ):
        from autoskillit.server import mcp

        original_run = FunctionTool.run

        async def run(tool, arguments):
            if tool.name == "kitchen_status":
                return ToolResult(
                    content=[TextContent(type="text", text="unrecoverable-original")],
                    structured_content={"result": "different-value"},
                )
            return await original_run(tool, arguments)

        monkeypatch.setattr(FunctionTool, "run", run)
        async with Client(mcp) as client:
            result = await client.call_tool("kitchen_status", {})

        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == _SCHEMA_NONCONFORMING_FAILURE
        assert "unrecoverable-original" not in result.content[0].text
        assert result.structured_content == {"result": _SCHEMA_NONCONFORMING_FAILURE}

    @pytest.mark.anyio
    async def test_real_client_boundary_spills_and_recovers_complete_producer_result(
        self,
        kitchen_enabled,
        tool_ctx_kitchen_open,
        monkeypatch,
        tmp_path,
    ):
        from autoskillit.server import mcp
        from autoskillit.server._response_budget import RESPONSE_SPILL_METADATA_KEY
        from autoskillit.server.tools import tools_execution

        sentinels = ("HEAD-SENTINEL", "MIDDLE-SENTINEL", "TAIL-SENTINEL")
        payload = {
            "success": True,
            "verdict": "GO",
            "result": (
                sentinels[0] + ("x" * 30_000) + sentinels[1] + ("y" * 30_000) + sentinels[2]
            ),
        }
        authoritative = json.dumps(payload)
        monkeypatch.setattr(
            tools_execution,
            "_import_and_call",
            AsyncMock(return_value=payload),
        )

        async with Client(mcp) as client:
            result = await client.call_tool(
                "run_python",
                {
                    "callable": "probe.module.callable",
                    "work_dir": str(tmp_path),
                },
            )

        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        final_text = result.content[0].text
        assert len(final_text.encode("utf-8")) <= (
            tool_ctx_kitchen_open.config.output_budget.response_max_bytes
        )
        assert result.structured_content == {"result": final_text}
        metadata = json.loads(final_text)[RESPONSE_SPILL_METADATA_KEY]
        artifact = Path(metadata["artifact_path"])
        assert artifact.read_text() == authoritative
        assert metadata["sha256"] == hashlib.sha256(authoritative.encode()).hexdigest()
        for sentinel in sentinels:
            assert sentinel in artifact.read_text()

    @pytest.mark.anyio
    async def test_call_next_is_awaited_exactly_once(self, kitchen_enabled):
        from autoskillit.server import mcp

        middleware = ResponseConformanceMiddleware(mcp)
        registered = await mcp.get_tool("kitchen_status")
        assert isinstance(registered, FunctionTool)
        converted = registered.convert_result("valid")
        call_next = AsyncMock(return_value=converted)
        context = MiddlewareContext(
            message=CallToolRequestParams(name="kitchen_status", arguments={}),
            method="tools/call",
            type="request",
        )

        assert await middleware.on_call_tool(context, call_next) is converted
        call_next.assert_awaited_once_with(context)

    @pytest.mark.anyio
    async def test_unknown_tool_not_found_is_unchanged(self):
        from autoskillit.server import mcp

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Unknown tool: 'not_a_real_tool'"):
                await client.call_tool("not_a_real_tool", {})

    @pytest.mark.anyio
    async def test_disabled_tool_not_found_is_unchanged(self):
        from autoskillit.server import mcp

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Unknown tool: 'kitchen_status'"):
                await client.call_tool("kitchen_status", {})
