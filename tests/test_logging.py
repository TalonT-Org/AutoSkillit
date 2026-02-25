"""Tests for autoskillit._logging — centralized structlog configuration."""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog


class TestGetLogger:
    def test_returns_bound_logger(self):
        """get_logger() returns a structlog BoundLogger callable."""
        from autoskillit._logging import get_logger

        logger = get_logger(__name__)
        assert callable(logger.info)
        assert callable(logger.debug)
        assert callable(logger.error)

    def test_module_name_used_as_logger_name(self):
        """get_logger(__name__) creates a logger named after the module."""
        from autoskillit._logging import get_logger

        with structlog.testing.capture_logs() as logs:
            get_logger("autoskillit.server").info("probe")
        assert logs[0]["logger"] == "autoskillit.server"

    def test_no_output_before_configure(self):
        """Before configure_logging(), the stdlib NullHandler suppresses all output."""
        stream = io.StringIO()
        # Attach a stream handler to root to catch any leakage
        root = logging.getLogger()  # noqa: TID251
        handler = logging.StreamHandler(stream)
        root.addHandler(handler)
        try:
            from autoskillit._logging import get_logger

            get_logger("autoskillit.test").warning("should_be_silent")
            # Only passes if autoskillit NullHandler suppresses propagation
            assert "should_be_silent" not in stream.getvalue()
        finally:
            root.removeHandler(handler)


class TestConfigureLogging:
    def test_text_output_reaches_stream(self):
        """configure_logging() routes log records to the given stream."""
        from autoskillit._logging import configure_logging, get_logger

        stream = io.StringIO()
        configure_logging(level=logging.DEBUG, json_output=False, stream=stream)
        get_logger("autoskillit.test").info("hello_world")
        assert "hello_world" in stream.getvalue()

    def test_json_output_is_valid_json(self):
        """json_output=True produces one valid JSON object per log line."""
        from autoskillit._logging import configure_logging, get_logger

        stream = io.StringIO()
        configure_logging(level=logging.DEBUG, json_output=True, stream=stream)
        get_logger("autoskillit.test").info("json_event", key="value")
        line = stream.getvalue().strip().splitlines()[-1]
        parsed = json.loads(line)
        assert parsed["event"] == "json_event"
        assert parsed["key"] == "value"

    def test_log_level_filters_below_threshold(self):
        """Messages below the configured level are suppressed."""
        from autoskillit._logging import configure_logging, get_logger

        stream = io.StringIO()
        configure_logging(level=logging.WARNING, json_output=False, stream=stream)
        get_logger("autoskillit.test").debug("suppressed_debug")
        assert "suppressed_debug" not in stream.getvalue()

    def test_never_writes_to_stdout(self, capsys: pytest.CaptureFixture[str]):
        """configure_logging() must never write to stdout (MCP protocol wire)."""
        from autoskillit._logging import configure_logging, get_logger

        configure_logging(level=logging.DEBUG, json_output=False)
        get_logger("autoskillit.test").info("stdout_check")
        captured = capsys.readouterr()
        assert captured.out == ""


class TestContextVarBinding:
    def test_bound_context_appears_in_all_records(self):
        """bind_contextvars enriches every log record in scope."""
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="run_skill", run_id="abc-123")
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            structlog.get_logger().info("event_a")
            structlog.get_logger().info("event_b")
        assert all(log["tool"] == "run_skill" for log in logs)
        assert all(log["run_id"] == "abc-123" for log in logs)

    def test_clear_removes_context(self):
        """clear_contextvars() removes all bound fields."""
        structlog.contextvars.bind_contextvars(tool="run_skill")
        structlog.contextvars.clear_contextvars()
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            structlog.get_logger().info("after_clear")
        assert "tool" not in logs[0]
