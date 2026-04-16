"""Tests for the track_response_size decorator in autoskillit.server.helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog

pytestmark = [pytest.mark.layer("server")]


class TestTrackResponseSize:
    @pytest.mark.anyio
    async def test_decorator_records_str_response(self):
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
            result = await fake_handler()

        assert result == response_str
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["tool_name"] == "get_token_summary"
        assert report[0]["response_bytes"] == len(response_str.encode("utf-8"))

    @pytest.mark.anyio
    async def test_decorator_serializes_dict_for_measurement(self):
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
            result = await fake_handler()

        assert result == response_dict  # original value returned unchanged
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["response_bytes"] == len(json.dumps(response_dict).encode("utf-8"))

    @pytest.mark.anyio
    async def test_decorator_noop_when_ctx_unavailable(self):
        """When _get_ctx_or_none() returns None, decorator is silent."""
        from autoskillit.server.helpers import track_response_size

        @track_response_size("run_skill")
        async def fake_handler():
            return "response"

        with patch("autoskillit.server.helpers._get_ctx_or_none", return_value=None):
            result = await fake_handler()

        assert result == "response"  # no error raised

    @pytest.mark.anyio
    async def test_decorator_catches_handler_exception_as_structured_json(self):
        """If the wrapped handler raises, the exception is caught and converted."""
        import json

        from autoskillit.server.helpers import track_response_size

        @track_response_size("run_skill")
        async def bad_handler():
            raise ValueError("something went wrong")

        with patch("autoskillit.server.helpers._get_ctx_or_none", return_value=None):
            result = await bad_handler()

        data = json.loads(result)
        assert data["success"] is False
        assert "ValueError: something went wrong" in data["error"]
        assert data["subtype"] == "tool_exception"

    @pytest.mark.anyio
    async def test_track_response_size_exception_envelope_includes_user_visible_message(self):
        """Exception envelope includes non-empty user_visible_message with tool name."""
        from autoskillit.server.helpers import track_response_size

        @track_response_size("open_kitchen")
        async def bad_handler():
            raise RuntimeError("boom")

        with patch("autoskillit.server.helpers._get_ctx_or_none", return_value=None):
            result = await bad_handler()

        data = json.loads(result)
        assert "user_visible_message" in data
        assert isinstance(data["user_visible_message"], str)
        assert len(data["user_visible_message"]) > 0
        assert "An internal error occurred in open_kitchen" in data["user_visible_message"]

    @pytest.mark.anyio
    async def test_track_response_size_exception_envelope_preserves_existing_fields(self):
        """Regression guard: success, error, exit_code, subtype keys still present."""
        from autoskillit.server.helpers import track_response_size

        @track_response_size("test_tool")
        async def bad_handler():
            raise ValueError("fail")

        with patch("autoskillit.server.helpers._get_ctx_or_none", return_value=None):
            result = await bad_handler()

        data = json.loads(result)
        assert data["success"] is False
        assert "ValueError: fail" in data["error"]
        assert data["exit_code"] == -1
        assert data["subtype"] == "tool_exception"
        assert "user_visible_message" in data
