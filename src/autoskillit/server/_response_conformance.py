"""Post-conversion conformance checks for registered string tools."""

from __future__ import annotations

import typing
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from fastmcp.tools.function_tool import FunctionTool
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import validator_for
from mcp.types import TextContent

from autoskillit.server._response_budget import emit_response_budget_failure

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext


_WRAPPED_STRING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
    "x-fastmcp-wrap-result": True,
}

_SCHEMA_NONCONFORMING_FAILURE = (
    '{"success":false,"error":"response_budget_failure","cause":"schema_nonconforming"}'
)


class ResponseConformanceDecision(StrEnum):
    """Exhaustive outcomes for the registered-handler-converted decision table."""

    BYPASS_NON_FUNCTION_TOOL = "bypass_non_function_tool"
    BYPASS_HANDLER_TYPE = "bypass_handler_type"
    BYPASS_REGISTERED_SCHEMA = "bypass_registered_schema"
    CONFORMING = "conforming"
    REWRITE_NONCONFORMING = "rewrite_nonconforming"


def decide_response_conformance(
    *,
    is_function_tool: bool,
    handler_returns_exact_str: bool,
    registered_schema_is_wrapped_string: bool,
    converted_result_conforms: bool,
) -> ResponseConformanceDecision:
    """Return the enforcement action for the four response representations."""
    if not is_function_tool:
        return ResponseConformanceDecision.BYPASS_NON_FUNCTION_TOOL
    if not handler_returns_exact_str:
        return ResponseConformanceDecision.BYPASS_HANDLER_TYPE
    if not registered_schema_is_wrapped_string:
        return ResponseConformanceDecision.BYPASS_REGISTERED_SCHEMA
    if converted_result_conforms:
        return ResponseConformanceDecision.CONFORMING
    return ResponseConformanceDecision.REWRITE_NONCONFORMING


def _handler_returns_exact_str(tool: FunctionTool) -> bool:
    try:
        return typing.get_type_hints(tool.fn).get("return") is str
    except (NameError, TypeError):
        return False


def _is_wrapped_string_schema(schema: dict[str, Any] | None) -> bool:
    return schema == _WRAPPED_STRING_OUTPUT_SCHEMA


def _converted_result_conforms(result: Any, schema: dict[str, Any]) -> bool:
    if not isinstance(result, ToolResult) or len(result.content) != 1:
        return False
    block = result.content[0]
    if not isinstance(block, TextContent):
        return False
    structured = result.structured_content
    if not isinstance(structured, dict) or structured.get("result") != block.text:
        return False
    try:
        validator_class = validator_for(schema)
        validator_class.check_schema(schema)
        validator_class(schema).validate(structured)
    except (SchemaError, ValidationError):
        return False
    return True


def _converted_text_utf8_bytes(result: Any) -> int:
    if not isinstance(result, ToolResult):
        return 0
    return sum(
        len(block.text.encode("utf-8"))
        for block in result.content
        if isinstance(block, TextContent)
    )


class ResponseConformanceMiddleware(Middleware):
    """Fail closed when FastMCP converts a registered string tool inconsistently."""

    def __init__(self, mcp: FastMCP) -> None:
        self._mcp = mcp

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        registered_tool = await self._mcp.get_tool(context.message.name)
        result = await call_next(context)

        if isinstance(registered_tool, FunctionTool):
            is_function_tool = True
            registered_schema = registered_tool.output_schema
            handler_returns_exact_str = _handler_returns_exact_str(registered_tool)
            registered_schema_is_wrapped_string = _is_wrapped_string_schema(registered_schema)
            converted_result_conforms = (
                registered_schema_is_wrapped_string
                and isinstance(registered_schema, dict)
                and _converted_result_conforms(result, registered_schema)
            )
        else:
            is_function_tool = False
            handler_returns_exact_str = False
            registered_schema_is_wrapped_string = False
            converted_result_conforms = False
        decision = decide_response_conformance(
            is_function_tool=is_function_tool,
            handler_returns_exact_str=handler_returns_exact_str,
            registered_schema_is_wrapped_string=registered_schema_is_wrapped_string,
            converted_result_conforms=converted_result_conforms,
        )
        if decision is ResponseConformanceDecision.REWRITE_NONCONFORMING:
            assert isinstance(registered_tool, FunctionTool)
            emit_response_budget_failure(
                context.message.name,
                "schema_nonconforming",
                _converted_text_utf8_bytes(result),
            )
            return registered_tool.convert_result(_SCHEMA_NONCONFORMING_FAILURE)
        return result


__all__ = [
    "ResponseConformanceDecision",
    "ResponseConformanceMiddleware",
    "decide_response_conformance",
]
