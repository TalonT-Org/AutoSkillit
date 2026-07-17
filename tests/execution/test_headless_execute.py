"""Tests for assert_headless_cmd CmdSpec validation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

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


class TestPreSessionIndexSignaling:
    """Tests that the caller logs the pre-session dirty-state signal."""

    @pytest.mark.anyio
    async def test_dirty_state_logged_with_structured_metadata(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        """When validate_pre_session_index returns True, the dirty-state
        warning must be emitted with structured kwargs (dirty=True, pre_sha=...)."""
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

        with (
            patch(
                "autoskillit.execution.headless._headless_execute.is_git_main_checkout",
                return_value=True,
            ),
            patch(
                "autoskillit.execution.headless._headless_execute.validate_pre_session_index",
                return_value=True,
            ),
            structlog.testing.capture_logs() as caplog,
        ):
            await run_headless_core("/test foo", str(tmp_path), minimal_ctx)

        dirty_events = [e for e in caplog if e.get("event") == "pre_session_index_reset"]
        assert dirty_events, f"pre_session_index_reset not logged; caplog={caplog}"
        assert dirty_events[0].get("dirty") is True
        assert "pre_sha" in dirty_events[0]

    @pytest.mark.anyio
    async def test_clean_state_does_not_log_dirty_warning(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        """When validate_pre_session_index returns False (clean state),
        no pre_session_index_reset warning should be emitted."""
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

        with (
            patch(
                "autoskillit.execution.headless._headless_execute.is_git_main_checkout",
                return_value=True,
            ),
            patch(
                "autoskillit.execution.headless._headless_execute.validate_pre_session_index",
                return_value=False,
            ) as validate_index,
            structlog.testing.capture_logs() as caplog,
        ):
            await run_headless_core("/test foo", str(tmp_path), minimal_ctx)

        validate_index.assert_awaited_once()
        dirty_events = [e for e in caplog if e.get("event") == "pre_session_index_reset"]
        assert not dirty_events, (
            f"pre_session_index_reset must NOT be logged when clean; caplog={caplog}"
        )
