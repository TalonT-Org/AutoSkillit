"""Shared test fixtures for autoskillit."""

import sys
from collections.abc import Generator
from pathlib import Path as _Path

import pytest
import structlog

from autoskillit.types import SubprocessResult, SubprocessRunner, TerminationReason


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


@pytest.fixture
def tool_ctx(monkeypatch, tmp_path):
    """Provide a fully isolated ToolContext for server tests.

    Monkeypatches server._ctx so all server tool calls use this context.
    Gate is enabled (open kitchen) by default — tests that need a closed
    gate should do: tool_ctx.gate = GateState(enabled=False) locally.
    """
    from autoskillit import server as _server
    from autoskillit._audit import AuditLog
    from autoskillit._context import ToolContext
    from autoskillit._gate import GateState
    from autoskillit._token_log import TokenLog
    from autoskillit.config import AutomationConfig

    mock_runner = MockSubprocessRunner()
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=AuditLog(),
        token_log=TokenLog(),
        gate=GateState(enabled=True),
        plugin_dir=str(tmp_path),
        runner=mock_runner,
    )
    monkeypatch.setattr(_server, "_ctx", ctx)
    return ctx


def _flush_logger_proxy_caches() -> None:
    import structlog._config as _sc

    current_procs = structlog.get_config()["processors"]

    for mod_name in list(sys.modules):
        if not mod_name.startswith("autoskillit"):
            continue
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        lg = getattr(mod, "logger", None)
        if lg is None:
            continue
        if isinstance(lg, _sc.BoundLoggerLazyProxy):
            lg.__dict__.pop("bind", None)
        elif hasattr(lg, "_processors"):
            # Resolved bound logger — reconnect to current processor list
            lg._processors = current_procs


@pytest.fixture(autouse=True)
def _reset_structlog():
    """Reset structlog config before each test.

    cache_logger_on_first_use=True caches the processor chain on first call.
    Tests that call configure_logging() must call _flush_logger_proxy_caches()
    to clear instance-level bind overrides from module-level proxies, because
    reset_defaults() creates a new processor list but does not remove the
    cached finalized_bind closure from existing BoundLoggerLazyProxy instances.
    """
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    _flush_logger_proxy_caches()
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    _flush_logger_proxy_caches()


@pytest.fixture(autouse=True)
def _reset_audit_log():
    """Clear the module-level _audit_log singleton before each test.

    Without this, failures recorded in one test class bleed into assertions
    in the next. The singleton is process-global — autouse ensures isolation.
    """
    from autoskillit._audit import _audit_log

    _audit_log.clear()
    yield
    _audit_log.clear()


@pytest.fixture(autouse=True)
def _reset_token_log() -> Generator[None, None, None]:
    """Clear the module-level _token_log singleton before each test."""
    from autoskillit._token_log import _token_log

    _token_log.clear()
    yield
    _token_log.clear()
