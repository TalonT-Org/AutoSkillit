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


# NOTE (groupE): After groupE creates tests/server/, move this fixture to
# tests/server/conftest.py so its scope can be safely narrowed.
# Do not move it before tests/server/ exists.
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


def _make_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    termination_reason: TerminationReason = TerminationReason.NATURAL_EXIT,
    data_confirmed: bool = True,
):
    """Create a SubprocessResult for mocking run_managed_async."""
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=termination_reason,
        pid=12345,
        data_confirmed=data_confirmed,
    )


def _make_timeout_result(stdout: str = "", stderr: str = ""):
    """Create a timed-out SubprocessResult."""
    return SubprocessResult(
        returncode=-1,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.TIMED_OUT,
        pid=12345,
    )
