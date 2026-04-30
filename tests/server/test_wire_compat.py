from __future__ import annotations

import pytest

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
