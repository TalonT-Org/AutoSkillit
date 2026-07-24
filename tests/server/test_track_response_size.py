"""Tests for the track_response_size decorator in autoskillit.server._notify."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import RECIPE_SECTION_RESPONSE_FLOOR_BYTES
from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _finalized_recipe_response():
    from autoskillit.server._recipe_delivery import FinalizedRecipeResponse

    ledger = MagicMock()
    ledger.commit.return_value = True
    ledger.abort.return_value = True
    finalized = FinalizedRecipeResponse(
        rendered="attested recipe response",
        decision=MagicMock(selected_result_token_limit=56_750),
        receipt_handle=MagicMock(),
        receipt_ledger=ledger,
    )
    return finalized, ledger


def _tracking_ctx():
    return MagicMock(
        response_log=MagicMock(record=MagicMock(return_value=False)),
        config=MagicMock(
            mcp_response=MagicMock(alert_threshold_tokens=0),
            output_budget=OutputBudgetConfig(),
        ),
    )


class TestTrackResponseSize:
    @pytest.mark.anyio
    async def test_decorator_records_str_response(self):
        """When the wrapped async handler returns a str, its byte length is recorded."""
        log = DefaultMcpResponseLog()
        response_str = json.dumps({"steps": [], "total": {}})

        from autoskillit.server._notify import track_response_size

        @track_response_size("get_token_summary")
        async def fake_handler():
            return response_str

        with patch("autoskillit.server._notify._get_ctx_or_none") as mock_ctx:
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

        from autoskillit.server._notify import track_response_size

        @track_response_size("kitchen_status")
        async def fake_handler():
            return response_dict

        with patch("autoskillit.server._notify._get_ctx_or_none") as mock_ctx:
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
        from autoskillit.server._notify import track_response_size

        @track_response_size("run_skill")
        async def fake_handler():
            return "response"

        with patch("autoskillit.server._notify._get_ctx_or_none", return_value=None):
            result = await fake_handler()

        assert result == "response"  # no error raised

    @pytest.mark.anyio
    async def test_decorator_catches_handler_exception_as_structured_json(self):
        """If the wrapped handler raises, the exception is caught and converted."""
        import json

        from autoskillit.server._notify import track_response_size

        @track_response_size("run_skill")
        async def bad_handler():
            raise ValueError("something went wrong")

        with patch("autoskillit.server._notify._get_ctx_or_none", return_value=None):
            result = await bad_handler()

        data = json.loads(result)
        assert data["success"] is False
        assert "ValueError: something went wrong" in data["error"]
        assert data["subtype"] == "tool_exception"

    @pytest.mark.anyio
    async def test_track_response_size_exception_envelope_includes_user_visible_message(self):
        """Exception envelope includes non-empty user_visible_message with tool name."""
        from autoskillit.server._notify import track_response_size

        @track_response_size("open_kitchen")
        async def bad_handler():
            raise RuntimeError("boom")

        with patch("autoskillit.server._notify._get_ctx_or_none", return_value=None):
            result = await bad_handler()

        data = json.loads(result)
        assert "user_visible_message" in data
        assert isinstance(data["user_visible_message"], str)
        assert len(data["user_visible_message"]) > 0
        assert "An internal error occurred in open_kitchen" in data["user_visible_message"]

    @pytest.mark.anyio
    async def test_track_response_size_exception_envelope_preserves_existing_fields(self):
        """Regression guard: success, error, exit_code, subtype keys still present."""
        from autoskillit.server._notify import track_response_size

        @track_response_size("test_tool")
        async def bad_handler():
            raise ValueError("fail")

        with patch("autoskillit.server._notify._get_ctx_or_none", return_value=None):
            result = await bad_handler()

        data = json.loads(result)
        assert data["success"] is False
        assert "ValueError: fail" in data["error"]
        assert data["exit_code"] == -1
        assert data["subtype"] == "tool_exception"
        assert "user_visible_message" in data

    @pytest.mark.anyio
    async def test_nonserializable_result_fails_closed_instead_of_returning_original(self):
        from autoskillit.server._notify import track_response_size

        original = {"private": object()}

        @track_response_size("nonserializable_tool")
        async def fake_handler():
            return original

        with patch("autoskillit.server._notify._get_ctx_or_none", return_value=None):
            result = await fake_handler()

        assert result is not original
        assert result["success"] is False
        assert "serialization_failed" in result["error"]

    @pytest.mark.anyio
    async def test_response_log_failure_is_nonfatal_and_does_not_log_exception_path(self):
        from autoskillit.server._notify import track_response_size

        response_log = MagicMock()
        response_log.record.side_effect = OSError("/private/project/response.json")
        ctx = MagicMock(
            response_log=response_log,
            config=MagicMock(mcp_response=MagicMock(alert_threshold_tokens=0)),
        )

        @track_response_size("small_tool")
        async def fake_handler():
            return "small"

        with (
            patch("autoskillit.server._notify._get_ctx_or_none", return_value=ctx),
            structlog.testing.capture_logs() as logs,
        ):
            result = await fake_handler()

        assert result == "small"
        assert "/private/project" not in repr(logs)
        assert any(log["event"] == "track_response_size_telemetry_failed" for log in logs)

    @pytest.mark.anyio
    async def test_notification_failure_is_nonfatal_and_does_not_log_exception_path(self):
        from autoskillit.server._notify import track_response_size

        class FakeContext:
            pass

        response_log = MagicMock()
        response_log.record.return_value = True
        ctx = MagicMock(
            response_log=response_log,
            config=MagicMock(mcp_response=MagicMock(alert_threshold_tokens=0)),
        )

        @track_response_size("notified_tool")
        async def fake_handler(_mcp_ctx):
            return "small"

        with (
            patch("autoskillit.server._notify._get_ctx_or_none", return_value=ctx),
            patch("fastmcp.Context", FakeContext),
            patch(
                "autoskillit.server._notify._notify",
                new=AsyncMock(side_effect=RuntimeError("/private/project/session.json")),
            ),
            structlog.testing.capture_logs() as logs,
        ):
            result = await fake_handler(FakeContext())

        assert result == "small"
        assert "/private/project" not in repr(logs)
        assert any(log["event"] == "track_response_size_notification_failed" for log in logs)

    @pytest.mark.anyio
    async def test_unexpected_enforcement_failure_is_bounded_and_centrally_emitted(self):
        from autoskillit.server._notify import track_response_size

        tool_name = "/private/tool/" + "x" * 200
        response_bound = max(200, RECIPE_SECTION_RESPONSE_FLOOR_BYTES)
        ctx = MagicMock(
            response_log=MagicMock(record=MagicMock(return_value=False)),
            config=MagicMock(
                mcp_response=MagicMock(alert_threshold_tokens=0),
                output_budget=OutputBudgetConfig(response_max_bytes=response_bound),
            ),
        )

        @track_response_size(tool_name)
        async def fake_handler():
            return "small"

        with (
            patch("autoskillit.server._notify._get_ctx_or_none", return_value=ctx),
            patch(
                "autoskillit.server._notify.enforce_response_budget",
                side_effect=RuntimeError("/private/project/enforcement.log"),
            ),
            patch("autoskillit.server._response_budget.logger.info") as log_info,
        ):
            result = await fake_handler()

        assert len(result.encode("utf-8")) <= response_bound
        assert "/private/project" not in result
        assert "/private/project" not in repr(log_info.call_args_list)
        event = next(
            call for call in log_info.call_args_list if call.args == ("response_budget_failure",)
        )
        assert event.kwargs["cause"] == "internal_invariant_failed"
        assert event.kwargs["original_utf8_bytes"] == len(b"small")

    @pytest.mark.anyio
    async def test_finalized_recipe_exact_response_commits_through_decorator(self):
        from autoskillit.server._notify import track_response_size

        finalized, ledger = _finalized_recipe_response()

        @track_response_size("open_kitchen")
        async def fake_handler():
            return finalized

        with (
            patch(
                "autoskillit.server._notify._get_ctx_or_none",
                return_value=_tracking_ctx(),
            ),
            patch(
                "autoskillit.server._notify.enforce_response_budget",
                return_value=finalized.rendered,
            ) as enforce,
        ):
            result = await fake_handler()

        assert result == finalized.rendered
        assert enforce.call_args.kwargs["selected_result_token_limit"] == 56_750
        ledger.commit.assert_called_once()
        ledger.abort.assert_not_called()

    @pytest.mark.parametrize(
        "enforced",
        ["transformed response", {"success": True, "artifact_path": "response.txt"}],
    )
    @pytest.mark.anyio
    async def test_finalized_recipe_transformation_aborts_through_decorator(self, enforced):
        from autoskillit.server._notify import track_response_size

        finalized, ledger = _finalized_recipe_response()

        @track_response_size("load_recipe")
        async def fake_handler():
            return finalized

        with (
            patch(
                "autoskillit.server._notify._get_ctx_or_none",
                return_value=_tracking_ctx(),
            ),
            patch(
                "autoskillit.server._notify.enforce_response_budget",
                return_value=enforced,
            ),
        ):
            result = await fake_handler()

        assert result == enforced
        ledger.commit.assert_not_called()
        ledger.abort.assert_called_once_with(finalized.receipt_handle)

    @pytest.mark.anyio
    async def test_finalized_recipe_enforcement_failure_aborts_through_decorator(self):
        from autoskillit.server._notify import track_response_size

        finalized, ledger = _finalized_recipe_response()
        bounded = {"success": False, "error": "response_budget_exceeded"}

        @track_response_size("open_kitchen")
        async def fake_handler():
            return finalized

        with (
            patch(
                "autoskillit.server._notify._get_ctx_or_none",
                return_value=_tracking_ctx(),
            ),
            patch(
                "autoskillit.server._notify.enforce_response_budget",
                side_effect=RuntimeError("enforcement failed"),
            ),
            patch(
                "autoskillit.server._notify.bounded_response_budget_failure",
                return_value=bounded,
            ),
        ):
            result = await fake_handler()

        assert result == bounded
        ledger.commit.assert_not_called()
        ledger.abort.assert_called_once_with(finalized.receipt_handle)
