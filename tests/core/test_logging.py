"""Tests for autoskillit.core.logging — centralized structlog configuration."""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog

from tests._helpers import _flush_structlog_proxy_caches as _flush_logger_proxy_caches


class TestGetLogger:
    def test_returns_bound_logger(self):
        """get_logger() returns a structlog BoundLogger callable."""
        from autoskillit.core.logging import get_logger

        logger = get_logger(__name__)
        assert callable(logger.info)
        assert callable(logger.debug)
        assert callable(logger.error)

    def test_module_name_used_as_logger_name(self):
        """get_logger(__name__) creates a logger named after the module."""
        from autoskillit.core.logging import get_logger

        with structlog.testing.capture_logs() as logs:
            get_logger("autoskillit.server").info("probe")
        assert logs, "Expected at least one log record"
        assert logs[0]["logger"] == "autoskillit.server"


class TestNullHandlerContract:
    def test_no_output_before_configure(self, capsys: pytest.CaptureFixture[str]):
        """NullHandler in autoskillit/__init__.py prevents stdlib lastResort output.

        Python 3.2+ invokes a lastResort handler that writes WARNING+ records to
        sys.stderr when no handlers are found anywhere in the logger hierarchy.
        The NullHandler installed in __init__.py satisfies the 'at least one
        handler found' condition, preventing lastResort from firing for any
        autoskillit.* stdlib logger before configure_logging() is called.
        """
        stdlib_logger = logging.getLogger("autoskillit.pre_configure_check")  # noqa: TID251
        stdlib_logger.warning("should_not_appear_before_configure")
        captured = capsys.readouterr()
        assert "should_not_appear_before_configure" not in captured.err
        assert "should_not_appear_before_configure" not in captured.out


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def _structlog_to_null(self):
        """Override the conftest autouse — _reset_structlog manages structlog state here."""
        yield  # no-op: _reset_structlog handles reset before and after each test

    @pytest.fixture(autouse=True)
    def _reset_structlog(self):
        structlog.reset_defaults()
        _flush_logger_proxy_caches()
        yield
        structlog.reset_defaults()
        _flush_logger_proxy_caches()

    def test_text_output_reaches_stream(self):
        """configure_logging() routes log records to the given stream."""
        from autoskillit.core.logging import configure_logging, get_logger

        stream = io.StringIO()
        configure_logging(level=logging.DEBUG, json_output=False, stream=stream)
        get_logger("autoskillit.test").info("hello_world")
        assert "hello_world" in stream.getvalue()

    def test_json_output_is_valid_json(self):
        """json_output=True produces one valid JSON object per log line."""
        from autoskillit.core.logging import configure_logging, get_logger

        stream = io.StringIO()
        configure_logging(level=logging.DEBUG, json_output=True, stream=stream)
        get_logger("autoskillit.test").info("json_event", key="value")
        line = stream.getvalue().strip().splitlines()[-1]
        parsed = json.loads(line)
        assert parsed["event"] == "json_event"
        assert parsed["key"] == "value"

    def test_log_level_filters_below_threshold(self):
        """Messages below the configured level are suppressed."""
        from autoskillit.core.logging import configure_logging, get_logger

        stream = io.StringIO()
        configure_logging(level=logging.WARNING, json_output=False, stream=stream)
        get_logger("autoskillit.test").debug("suppressed_debug")
        assert "suppressed_debug" not in stream.getvalue()

    def test_never_writes_to_stdout(self, capsys: pytest.CaptureFixture[str]):
        """configure_logging() must never write to stdout (MCP protocol wire)."""
        from autoskillit.core.logging import configure_logging, get_logger

        configure_logging(level=logging.DEBUG, json_output=False)
        get_logger("autoskillit.test").info("stdout_check")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "stdout_check" in captured.err

    def test_configure_logging_level_debug(self):
        """configure_logging(level=DEBUG) sets structlog filter to DEBUG."""
        from autoskillit.core.logging import configure_logging, get_logger

        stream = io.StringIO()
        configure_logging(level=logging.DEBUG, json_output=False, stream=stream)
        get_logger("autoskillit.test").debug("debug_probe")
        assert "debug_probe" in stream.getvalue()
        # Also verify stdlib logger is at DEBUG
        stdlib_logger = logging.getLogger("autoskillit")  # noqa: TID251
        assert stdlib_logger.level == logging.DEBUG

    def test_configure_logging_safe_to_call_twice(self):
        """configure_logging() can be called twice (two-phase boot)."""
        from autoskillit.core.logging import configure_logging, get_logger

        stream1 = io.StringIO()
        configure_logging(level=logging.INFO, json_output=False, stream=stream1)
        stream2 = io.StringIO()
        configure_logging(level=logging.DEBUG, json_output=False, stream=stream2)
        _flush_logger_proxy_caches()
        get_logger("autoskillit.test").debug("second_config_probe")
        assert "second_config_probe" in stream2.getvalue()


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
        assert logs, "Expected at least one log record"
        assert "tool" not in logs[0]
