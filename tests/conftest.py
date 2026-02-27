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


@pytest.fixture
def tool_ctx(monkeypatch, tmp_path):
    """Provide a fully isolated ToolContext for server tests.

    Monkeypatches server._ctx so all server tool calls use this context.
    Gate is enabled (open kitchen) by default — tests that need a closed
    gate should do: tool_ctx.gate = GateState(enabled=False) locally.
    """
    from autoskillit import server as _server
    from autoskillit.config import AutomationConfig
    from autoskillit.pipeline.audit import AuditLog
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.pipeline.gate import GateState
    from autoskillit.pipeline.tokens import TokenLog

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
