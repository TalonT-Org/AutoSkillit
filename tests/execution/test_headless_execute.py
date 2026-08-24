"""Tests for headless physical execution behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

from autoskillit.core import CmdSpec
from autoskillit.core.types import SubprocessResult, TerminationReason
from tests.execution.conftest import _sink_env

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


def _install_fake_sink(
    monkeypatch,
    execute_module,
    events: list[str],
    expected_log_dir: str,
    *,
    close_raises: bool,
) -> dict[str, str]:
    sink_env = _sink_env()

    class FakeSink:
        env = sink_env

        @classmethod
        def start(cls, log_dir: str) -> FakeSink:
            assert log_dir == expected_log_dir
            events.append("start")
            return cls()

        def close(self) -> None:
            events.append("close")
            if close_raises:
                raise OSError("best-effort sink shutdown")

    monkeypatch.setattr(execute_module, "LocalOtlpSink", FakeSink)
    return sink_env


def _conflicting_sink_env(sink_env: dict[str, str]) -> dict[str, str]:
    return {key: f"conflicting-{key.lower()}" for key in sink_env}


@pytest.mark.anyio
@pytest.mark.parametrize("close_raises", (False, True))
async def test_execute_overlays_sink_endpoint_and_always_closes_it(
    minimal_ctx, tmp_path: Path, monkeypatch, close_raises: bool
) -> None:
    import autoskillit.execution.headless._headless_execute as _execute_module
    import autoskillit.execution.session_log as _session_log
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import _execute_claude_headless
    from tests.execution.conftest import _launch_preparation, _mock_backend
    from tests.fakes import MockSubprocessRunner

    events: list[str] = []
    runner = MockSubprocessRunner()
    runner.set_default(_success_result())
    minimal_ctx.runner = runner
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    parent_environment = dict(os.environ)
    sink_env = _install_fake_sink(
        monkeypatch,
        _execute_module,
        events,
        minimal_ctx.config.linux_tracing.log_dir,
        close_raises=close_raises,
    )
    monkeypatch.setattr(
        _session_log,
        "flush_session_log",
        lambda **_kwargs: events.append("flush"),
    )

    def build_spec(_binding, provider_extras):
        return ClaudeHeadlessCmd(
            cmd=("claude", "-p", "test"),
            env=dict(provider_extras or {}),
        )

    result = await _execute_claude_headless(
        build_spec,
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        provider_extras=_conflicting_sink_env(sink_env),
        step_name="sink-test",
        launch_resolver=minimal_ctx.launch_resolver,
        launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
    )

    assert result.success
    assert events == ["start", "flush", "close"]
    runner_env = runner.call_args_list[0][3]["env"]
    assert {key: runner_env[key] for key in sink_env} == sink_env
    assert os.environ == parent_environment


@pytest.mark.anyio
async def test_sink_close_failure_does_not_replace_runner_crash(
    minimal_ctx, tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution as _execution
    import autoskillit.execution.headless._headless_execute as _execute_module
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import _execute_claude_headless
    from tests.execution.conftest import _launch_preparation, _mock_backend

    events: list[str] = []
    runner_envs: list[dict[str, str]] = []
    parent_environment = dict(os.environ)
    sink_env = _install_fake_sink(
        monkeypatch,
        _execute_module,
        events,
        minimal_ctx.config.linux_tracing.log_dir,
        close_raises=True,
    )

    async def crashing_runner(_cmd, **kwargs):
        runner_envs.append(dict(kwargs["env"]))
        raise RuntimeError("runner crashed")

    minimal_ctx.runner = crashing_runner  # type: ignore[assignment]
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    monkeypatch.setattr(
        _execution,
        "flush_session_log",
        lambda **_kwargs: events.append("flush"),
    )

    result = await _execute_claude_headless(
        lambda _binding, extras: ClaudeHeadlessCmd(
            cmd=("claude", "-p", "test"), env=dict(extras or {})
        ),
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        provider_extras=_conflicting_sink_env(sink_env),
        step_name="sink-crash-test",
        launch_resolver=minimal_ctx.launch_resolver,
        launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
    )

    assert result.subtype == "crashed"
    assert result.result.startswith("RuntimeError: runner crashed")
    assert events == ["start", "flush", "close"]
    assert {key: runner_envs[0][key] for key in sink_env} == sink_env
    assert os.environ == parent_environment


@pytest.mark.anyio
async def test_sink_close_failure_does_not_replace_propagated_infrastructure_fault(
    minimal_ctx, tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution.headless._headless_execute as _execute_module
    from autoskillit.core import InfrastructureFaultError, RetryReason, SkillResult
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import _execute_claude_headless
    from tests.execution.conftest import _launch_preparation, _mock_backend
    from tests.fakes import MockSubprocessRunner

    events: list[str] = []
    runner = MockSubprocessRunner()
    runner.set_default(_success_result())
    minimal_ctx.runner = runner
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    parent_environment = dict(os.environ)
    sink_env = _install_fake_sink(
        monkeypatch,
        _execute_module,
        events,
        minimal_ctx.config.linux_tracing.log_dir,
        close_raises=True,
    )
    retry_result = SkillResult(
        success=False,
        result="missing output contract",
        session_id="nudge-session",
        subtype="contract_recovery",
        is_error=False,
        exit_code=1,
        needs_retry=True,
        retry_reason=RetryReason.CONTRACT_RECOVERY,
        stderr="",
    )
    fault = InfrastructureFaultError("nudge infrastructure failed")

    async def raising_nudge(*_args, **_kwargs):
        raise fault

    monkeypatch.setattr(
        _execute_module,
        "_build_skill_result",
        lambda *_args, **_kwargs: retry_result,
    )
    monkeypatch.setattr(_execute_module, "_attempt_contract_nudge", raising_nudge)

    with pytest.raises(InfrastructureFaultError) as exc_info:
        await _execute_claude_headless(
            lambda _binding, extras: ClaudeHeadlessCmd(
                cmd=("claude", "-p", "test"), env=dict(extras or {})
            ),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_extras=_conflicting_sink_env(sink_env),
            launch_resolver=minimal_ctx.launch_resolver,
            launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
        )

    assert exc_info.value is fault
    assert events == ["start", "close"]
    runner_env = runner.call_args_list[0][3]["env"]
    assert {key: runner_env[key] for key in sink_env} == sink_env
    assert os.environ == parent_environment


@pytest.mark.anyio
async def test_sink_close_failure_does_not_replace_deferred_cancellation(
    minimal_ctx, tmp_path: Path, monkeypatch
) -> None:
    import anyio

    import autoskillit.execution as _execution
    import autoskillit.execution.headless._headless_execute as _execute_module
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import _execute_claude_headless
    from tests.execution.conftest import _launch_preparation, _mock_backend

    events: list[str] = []
    runner_envs: list[dict[str, str]] = []
    parent_environment = dict(os.environ)
    sink_env = _install_fake_sink(
        monkeypatch,
        _execute_module,
        events,
        minimal_ctx.config.linux_tracing.log_dir,
        close_raises=True,
    )

    async def cancelling_runner(_cmd, **kwargs):
        runner_envs.append(dict(kwargs["env"]))
        raise anyio.get_cancelled_exc_class()()

    minimal_ctx.runner = cancelling_runner  # type: ignore[assignment]
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    monkeypatch.setattr(
        _execution,
        "flush_session_log",
        lambda **_kwargs: events.append("flush"),
    )

    with pytest.raises(anyio.get_cancelled_exc_class()):
        await _execute_claude_headless(
            lambda _binding, extras: ClaudeHeadlessCmd(
                cmd=("claude", "-p", "test"), env=dict(extras or {})
            ),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_extras=_conflicting_sink_env(sink_env),
            step_name="sink-cancellation-test",
            launch_resolver=minimal_ctx.launch_resolver,
            launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
        )

    assert events == ["start", "flush", "close"]
    assert {key: runner_envs[0][key] for key in sink_env} == sink_env
    assert os.environ == parent_environment


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
