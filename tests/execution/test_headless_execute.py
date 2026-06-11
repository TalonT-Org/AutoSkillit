"""Tests for assert_headless_cmd CmdSpec validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import CmdSpec
from autoskillit.core.types import SubprocessResult, TerminationReason

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _success_result() -> SubprocessResult:
    return SubprocessResult(
        returncode=0,
        stdout=json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "sess-idle-test",
            }
        ),
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )


def test_assert_headless_cmd_passes_with_p_flag() -> None:
    from autoskillit.execution.headless._headless_helpers import assert_headless_cmd

    assert_headless_cmd(CmdSpec(cmd=("claude", "-p", "prompt"), env={}))


def test_assert_headless_cmd_raises_without_p_flag() -> None:
    from autoskillit.execution.headless._headless_helpers import assert_headless_cmd

    with pytest.raises(ValueError, match=r"-p flag"):
        assert_headless_cmd(CmdSpec(cmd=("claude", "--dangerously-skip-permissions"), env={}))


def test_assert_headless_cmd_non_claude_binary_exempt() -> None:
    from autoskillit.execution.headless._headless_helpers import assert_headless_cmd

    assert_headless_cmd(CmdSpec(cmd=("codex", "exec", "prompt"), env={}))


def test_assert_headless_cmd_empty_cmd_no_error() -> None:
    from autoskillit.execution.headless._headless_helpers import assert_headless_cmd

    assert_headless_cmd(CmdSpec(cmd=(), env={}))


class TestProcessIdleTimeoutOverride:
    """Tests for CmdSpec.process_idle_timeout_ms overriding effective_idle."""

    @pytest.mark.anyio
    async def test_spec_idle_used_when_caller_supplies_none(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _mock_backend
        from tests.fakes import MockSubprocessRunner

        monkeypatch.delenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", raising=False)
        runner = MockSubprocessRunner()
        runner.set_default(_success_result())
        minimal_ctx.runner = runner
        backend = _mock_backend(pty_required=True, channel_b_capable=True)
        backend.build_skill_session_cmd.return_value = CmdSpec(
            cmd=("claude", "-p", "test"),
            env={},
            process_idle_timeout_ms=30000,
        )
        minimal_ctx.backend = backend

        await run_headless_core("/test foo", str(tmp_path), minimal_ctx)

        assert runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        assert kwargs.get("idle_output_timeout") == 30.0

    @pytest.mark.anyio
    async def test_spec_idle_overrides_when_smaller(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _mock_backend
        from tests.fakes import MockSubprocessRunner

        monkeypatch.setenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", "45")
        runner = MockSubprocessRunner()
        runner.set_default(_success_result())
        minimal_ctx.runner = runner
        backend = _mock_backend(pty_required=True, channel_b_capable=True)
        backend.build_skill_session_cmd.return_value = CmdSpec(
            cmd=("claude", "-p", "test"),
            env={},
            process_idle_timeout_ms=15000,
        )
        minimal_ctx.backend = backend

        await run_headless_core("/test foo", str(tmp_path), minimal_ctx)

        assert runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        assert kwargs.get("idle_output_timeout") == 15.0

    @pytest.mark.anyio
    async def test_zero_spec_idle_leaves_effective_unaffected(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        from autoskillit.execution.headless import run_headless_core
        from tests.execution.conftest import _mock_backend
        from tests.fakes import MockSubprocessRunner

        monkeypatch.setenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", "30")
        runner = MockSubprocessRunner()
        runner.set_default(_success_result())
        minimal_ctx.runner = runner
        backend = _mock_backend(pty_required=True, channel_b_capable=True)
        backend.build_skill_session_cmd.return_value = CmdSpec(
            cmd=("claude", "-p", "test"),
            env={},
            process_idle_timeout_ms=0,
        )
        minimal_ctx.backend = backend

        await run_headless_core("/test foo", str(tmp_path), minimal_ctx)

        assert runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        assert kwargs.get("idle_output_timeout") == 30.0
