"""Tests for the track_response_size decorator in autoskillit.server.helpers."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog


class TestTrackResponseSize:
    def test_decorator_records_str_response(self):
        """When the wrapped async handler returns a str, its byte length is recorded."""
        log = DefaultMcpResponseLog()
        response_str = json.dumps({"steps": [], "total": {}})

        from autoskillit.server.helpers import track_response_size

        @track_response_size("get_token_summary")
        async def fake_handler():
            return response_str

        with patch("autoskillit.server.helpers._get_ctx_or_none") as mock_ctx:
            mock_ctx.return_value = MagicMock(
                response_log=log,
                config=MagicMock(mcp_response=MagicMock(alert_threshold_tokens=0)),
            )
            result = asyncio.get_event_loop().run_until_complete(fake_handler())

        assert result == response_str
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["tool_name"] == "get_token_summary"
        assert report[0]["response_bytes"] == len(response_str.encode("utf-8"))

    def test_decorator_serializes_dict_for_measurement(self):
        """When handler returns a dict, it's serialized to measure byte length."""
        log = DefaultMcpResponseLog()
        response_dict = {"key": "value"}

        from autoskillit.server.helpers import track_response_size

        @track_response_size("kitchen_status")
        async def fake_handler():
            return response_dict

        with patch("autoskillit.server.helpers._get_ctx_or_none") as mock_ctx:
            mock_ctx.return_value = MagicMock(
                response_log=log,
                config=MagicMock(mcp_response=MagicMock(alert_threshold_tokens=0)),
            )
            result = asyncio.get_event_loop().run_until_complete(fake_handler())

        assert result == response_dict  # original value returned unchanged
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["response_bytes"] == len(json.dumps(response_dict).encode("utf-8"))

    def test_decorator_noop_when_ctx_unavailable(self):
        """When _get_ctx_or_none() returns None, decorator is silent."""
        from autoskillit.server.helpers import track_response_size

        @track_response_size("run_skill")
        async def fake_handler():
            return "response"

        with patch("autoskillit.server.helpers._get_ctx_or_none", return_value=None):
            result = asyncio.get_event_loop().run_until_complete(fake_handler())

        assert result == "response"  # no error raised

    def test_decorator_does_not_suppress_handler_exception(self):
        """If the wrapped handler raises, the exception propagates normally."""
        from autoskillit.server.helpers import track_response_size

        @track_response_size("run_skill")
        async def bad_handler():
            raise ValueError("something went wrong")

        with pytest.raises(ValueError, match="something went wrong"):
            asyncio.get_event_loop().run_until_complete(bad_handler())
