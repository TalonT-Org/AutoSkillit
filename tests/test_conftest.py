"""Tests for conftest fixture infrastructure: tool_ctx and MockSubprocessRunner."""

from pathlib import Path

from autoskillit.types import SubprocessResult, TerminationReason


def test_tool_ctx_provides_isolated_gate(tool_ctx):
    """tool_ctx fixture provides a ToolContext with gate enabled."""
    from autoskillit._gate import GateState

    assert isinstance(tool_ctx.gate, GateState)
    assert tool_ctx.gate.enabled is True


def test_tool_ctx_provides_isolated_audit(tool_ctx):
    """tool_ctx fixture provides a fresh AuditLog with no records."""
    assert tool_ctx.audit.get_report() == []


def test_tool_ctx_provides_isolated_token_log(tool_ctx):
    """tool_ctx fixture provides a fresh TokenLog with no entries."""
    assert tool_ctx.token_log.get_report() == []


async def test_mock_subprocess_runner_push_and_pop():
    """MockSubprocessRunner.push() queues results, __call__ pops in order."""
    from tests.conftest import MockSubprocessRunner

    runner = MockSubprocessRunner()
    r1 = SubprocessResult(0, "out1", "", TerminationReason.NATURAL_EXIT, 100)
    r2 = SubprocessResult(1, "out2", "err", TerminationReason.NATURAL_EXIT, 101)
    runner.push(r1)
    runner.push(r2)

    got1 = await runner(["cmd"], cwd=Path("/tmp"), timeout=30.0)
    got2 = await runner(["cmd"], cwd=Path("/tmp"), timeout=30.0)
    assert got1 is r1
    assert got2 is r2


async def test_mock_subprocess_runner_default_when_empty():
    """MockSubprocessRunner returns a zero-exit default when queue is empty."""
    from tests.conftest import MockSubprocessRunner

    runner = MockSubprocessRunner()
    result = await runner(["cmd"], cwd=Path("/tmp"), timeout=30.0)
    assert result.returncode == 0
