"""Shared test fixtures for autoskillit."""

import sys
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


def _flush_structlog_proxy_caches() -> None:
    """Repair any autoskillit loggers cached before this fixture ran.

    Secondary defense only — the primary mechanism is cache_logger_on_first_use=False
    set at fixture entry, which prevents new caching during the test. This flush
    handles the edge case of module-level loggers cached at import time.

    Scans ALL module attributes (not just 'logger'/'_logger') so that loggers
    stored under any name (e.g. '_log' in execution.quota) are repaired.
    """
    import structlog
    import structlog._config as _sc

    current_procs = structlog.get_config()["processors"]
    for mod_name in list(sys.modules):
        if not mod_name.startswith("autoskillit"):
            continue
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for lg in vars(mod).values():
            if isinstance(lg, _sc.BoundLoggerLazyProxy):
                lg.__dict__.pop("bind", None)
            elif hasattr(lg, "_processors"):
                lg._processors = current_procs


@pytest.fixture(autouse=True)
def _structlog_to_null():
    """Prevent structlog from writing to stdout in any test.

    In the default state (before configure_logging() is called), structlog's
    PrintLoggerFactory routes all log output to sys.stdout. Tests that use
    capsys to inspect stdout are silently corrupted when a mock bypass causes
    a real production function to log.

    Two-layer isolation strategy:

    1. Primary: ``structlog.configure(cache_logger_on_first_use=False)`` — the
       official structlog recommendation for test environments. Prevents proxy
       caches from being populated during tests, so ``reset_defaults()`` is
       sufficient after each test without manual cache surgery.

    2. Secondary: ``_flush_structlog_proxy_caches()`` — repairs loggers that
       were cached before this fixture ran (e.g., module-level loggers cached
       at import time before the fixture had a chance to set
       cache_logger_on_first_use=False).

    Then wraps the test in ``capture_logs()`` to drop all log output.

    Note: TestConfigureLogging in test_logging.py has its own class-scoped
    ``_structlog_to_null`` no-op override and ``_reset_structlog`` fixture that
    owns structlog state management for those tests.
    """
    import structlog
    import structlog.testing

    structlog.configure(cache_logger_on_first_use=False)
    _flush_structlog_proxy_caches()
    with structlog.testing.capture_logs():
        yield
    structlog.reset_defaults()


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
