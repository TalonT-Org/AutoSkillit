"""Tests for the exception boundary in track_response_size.

Verifies that unhandled exceptions in tool handlers are caught by the
track_response_size decorator and converted to structured error JSON
instead of propagating to FastMCP.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog

pytestmark = [pytest.mark.layer("server")]


class TestToolExceptionBoundary:
    @pytest.mark.anyio
    async def test_exception_produces_structured_error_json(self):
        from autoskillit.server.helpers import track_response_size

        @track_response_size("test_tool")
        async def boom():
            raise RuntimeError("boom")

        with patch("autoskillit.server.helpers._get_ctx_or_none") as mock_ctx:
            log = DefaultMcpResponseLog()
            mock_ctx.return_value = MagicMock(
                response_log=log,
                config=MagicMock(mcp_response=MagicMock(alert_threshold_tokens=0)),
            )
            result = await boom()

        data = json.loads(result)
        assert data["success"] is False
        assert data["error"] == "RuntimeError: boom"
        assert data["exit_code"] == -1
        assert data["subtype"] == "tool_exception"

    @pytest.mark.anyio
    async def test_assertion_error_produces_structured_error(self):
        from autoskillit.server.helpers import track_response_size

        @track_response_size("test_tool")
        async def bad():
            raise AssertionError("No subprocess runner configured")

        with patch("autoskillit.server.helpers._get_ctx_or_none", return_value=None):
            result = await bad()

        data = json.loads(result)
        assert data["success"] is False
        assert "No subprocess runner configured" in data["error"]

    @pytest.mark.anyio
    async def test_os_error_produces_structured_error(self):
        from autoskillit.server.helpers import track_response_size

        @track_response_size("test_tool")
        async def bad():
            raise OSError("[Errno 2] No such file or directory: '/bad/path'")

        with patch("autoskillit.server.helpers._get_ctx_or_none", return_value=None):
            result = await bad()

        data = json.loads(result)
        assert data["exit_code"] == -1
        assert "/bad/path" in data["error"]

    @pytest.mark.anyio
    async def test_normal_return_passes_through_unchanged(self):
        from autoskillit.server.helpers import track_response_size

        expected = '{"success": true, "result": "ok"}'

        @track_response_size("test_tool")
        async def ok():
            return expected

        with patch("autoskillit.server.helpers._get_ctx_or_none", return_value=None):
            result = await ok()

        assert result == expected

    @pytest.mark.anyio
    async def test_response_size_still_recorded_on_exception(self):
        from autoskillit.server.helpers import track_response_size

        @track_response_size("test_tool")
        async def bad():
            raise ValueError("kaboom")

        log = DefaultMcpResponseLog()
        with patch("autoskillit.server.helpers._get_ctx_or_none") as mock_ctx:
            mock_ctx.return_value = MagicMock(
                response_log=log,
                config=MagicMock(mcp_response=MagicMock(alert_threshold_tokens=0)),
            )
            result = await bad()

        report = log.get_report()
        assert len(report) == 1
        assert report[0]["tool_name"] == "test_tool"
        assert report[0]["response_bytes"] == len(result.encode("utf-8"))
