"""Shared test fixtures for autoskillit."""

from pathlib import Path as _Path

import pytest

from autoskillit.core.types import SubprocessResult, SubprocessRunner, TerminationReason


class MockSubprocessRunner(SubprocessRunner):
    """Test double for SubprocessRunner. Queues predetermined results.

    Inherits from SubprocessRunner (Protocol) so mypy verifies the __call__
    signature matches the protocol at class definition, not just at call sites.
    """

    def __init__(self) -> None:
        self._queue: list[SubprocessResult] = []
        self._default = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=99999,
        )
        self.call_args_list: list[tuple] = []

    def push(self, result: SubprocessResult) -> None:
        """Queue a result to be returned by the next __call__."""
        self._queue.append(result)

    def set_default(self, result: SubprocessResult) -> None:
        """Set the result returned when the queue is empty."""
        self._default = result

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: _Path,
        timeout: float,
        **kwargs: object,
    ) -> SubprocessResult:
        self.call_args_list.append((cmd, cwd, timeout, kwargs))
        if self._queue:
            return self._queue.pop(0)
        return self._default


@pytest.fixture(autouse=True)
def _structlog_to_null():
    """Prevent structlog from writing to stdout in any test.

    In the default state (before configure_logging() is called), structlog's
    PrintLoggerFactory routes all log output to sys.stdout. Tests that use
    capsys to inspect stdout are silently corrupted when a mock bypass causes
    a real production function to log.

    This autouse fixture wraps every test in structlog.testing.capture_logs(),
    which prepends a CapturingProcessor to the current processor chain. Log
    events are captured as dicts and a DropEvent is raised before the message
    reaches the underlying PrintLogger. No log call can reach stdout.

    Works in the default state because module-level loggers share the same
    processor list reference as structlog.get_config()["processors"] until
    configure_logging() is called.

    Note: TestConfigureLogging in test_logging.py has its own class-scoped
    _reset_structlog autouse fixture that calls structlog.reset_defaults().
    This resets the processor list, which would break the capture_logs()
    context still active from this fixture. A class-level no-op override
    is added to TestConfigureLogging to prevent this conflict.
    """
    import structlog.testing

    with structlog.testing.capture_logs():
        yield


@pytest.fixture
def parse_stdout_json(capsys):
    """Parse capsys-captured stdout as JSON with diagnostic context on failure.

    Replaces bare ``json.loads(capsys.readouterr().out)`` calls. When parsing
    fails, raises AssertionError showing the full raw stdout and stderr content,
    so the developer immediately sees what was captured rather than getting an
    opaque JSONDecodeError with no context.

    Usage::

        def test_quota_status_outputs_json(self, monkeypatch, parse_stdout_json, tmp_path):
            cli.quota_status()
            data = parse_stdout_json()
            assert "should_sleep" in data
    """
    import json

    def _parse() -> dict:
        captured = capsys.readouterr()
        try:
            return json.loads(captured.out)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"stdout is not valid JSON.\n"
                f"  parse error : {exc}\n"
                f"  stdout      : {captured.out!r}\n"
                f"  stderr      : {captured.err!r}"
            ) from exc

    return _parse


@pytest.fixture
def tool_ctx(monkeypatch, tmp_path):
    """Provide a fully isolated ToolContext for server tests.

    Monkeypatches server._ctx so all server tool calls use this context.
    Gate is enabled (open kitchen) by default — tests that need a closed
    gate should do: tool_ctx.gate = DefaultGateState(enabled=False) locally.

    All service fields (executor, tester, db_reader, workspace_mgr, recipes,
    migrations) are wired via make_context() so routing tests work correctly.
    """
    from autoskillit import server as _server
    from autoskillit.config import AutomationConfig
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server._factory import make_context

    mock_runner = MockSubprocessRunner()
    ctx = make_context(AutomationConfig(), runner=mock_runner, plugin_dir=str(tmp_path))
    ctx.gate = DefaultGateState(enabled=True)
    monkeypatch.setattr(_server, "_ctx", ctx)
    return ctx
