"""Tests for headless physical execution behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

import autoskillit.execution.headless._headless_execute as _patch_headless__headless_execute
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
    evidence: dict[str, tuple[str, tuple[dict[str, object], ...]]] | None = None,
    token_evidence: dict[str, dict[str, object]] | None = None,
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

        def model_evidence_for(self, session_id: str):
            events.append(f"lookup:{session_id}")
            return (evidence or {}).get(session_id, ("", ()))

        def token_usage_for(self, session_id: str, _backend: str, _provider: str):
            events.append(f"token_lookup:{session_id}")
            return (token_evidence or {}).get(session_id)

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
    import autoskillit.execution.session_log.session_log as _session_log
    from autoskillit.core import ModelIdentity, SessionTelemetry
    from autoskillit.execution.headless import _execute_claude_headless
    from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
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
        evidence={
            "sess-idle-test": (
                "gpt-5.6-sol",
                (
                    {
                        "model": "claude-sonnet-5",
                        "final_model": "claude-opus-5",
                        "model_swapped": True,
                    },
                ),
            )
        },
    )
    flush_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        _session_log,
        "flush_session_log",
        lambda **kwargs: (events.append("flush"), flush_calls.append(kwargs)),
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
        model_identity=ModelIdentity(
            configured_model="opus",
            effective_model="opus",
            profile_name="",
        ),
    )

    assert result.success
    assert events == [
        "start",
        "close",
        "lookup:sess-idle-test",
        *([] if close_raises else ["token_lookup:sess-idle-test"]),
        "flush",
        "close",
    ]
    assert flush_calls[0]["session_id"] == "sess-idle-test"
    resolved_identity = flush_calls[0]["model_identity"]
    telemetry = flush_calls[0]["telemetry"]
    assert isinstance(resolved_identity, ModelIdentity)
    assert isinstance(telemetry, SessionTelemetry)
    assert resolved_identity.configured_model == "opus"
    assert resolved_identity.effective_model == "gpt-5.6-sol"
    assert telemetry.subagent_model_outcomes[0]["model_swapped"] is True
    runner_env = runner.call_args_list[0][3]["env"]
    assert {key: runner_env[key] for key in sink_env} == sink_env
    assert os.environ == parent_environment


@pytest.mark.anyio
async def test_correlated_otlp_tokens_replace_parser_totals_before_logging_and_flush(
    minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.execution.evidence.session_log as session_log
    import autoskillit.execution.headless._headless_execute as execute_module
    from autoskillit.execution.headless import _execute_claude_headless
    from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
    from tests.execution.conftest import _launch_preparation, _mock_backend
    from tests.fakes import MockSubprocessRunner

    parser_record = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "sess-idle-test",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 10,
            },
        }
    )
    runner = MockSubprocessRunner()
    runner.set_default(
        SubprocessResult(
            returncode=0,
            stdout=parser_record,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )
    )
    minimal_ctx.runner = runner
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    selected = {
        "backend": "claude-code",
        "provider_used": "anthropic",
        "input_tokens": {"state": "measured", "value": 2},
        "output_tokens": {"state": "measured", "value": 4},
        "cache_read_tokens": {"state": "measured_zero", "value": 0},
        "cache_write_tokens": {"state": "measured", "value": 36520},
        "peak_context": {"state": "measured_zero", "value": 0},
    }
    events: list[str] = []
    _install_fake_sink(
        monkeypatch,
        execute_module,
        events,
        minimal_ctx.config.linux_tracing.log_dir,
        close_raises=False,
        token_evidence={"sess-idle-test": selected},
    )
    flushed: list[dict[str, object]] = []
    monkeypatch.setattr(session_log, "flush_session_log", lambda **kwargs: flushed.append(kwargs))

    result = await _execute_claude_headless(
        lambda _binding, extras: ClaudeHeadlessCmd(
            cmd=("claude", "-p", "test"), env=dict(extras or {})
        ),
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        step_name="sink-token-preference",
        launch_resolver=minimal_ctx.launch_resolver,
        launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
    )

    assert result.token_usage is not None
    assert result.token_usage["input_tokens"] == {"state": "measured", "value": 2}
    assert minimal_ctx.token_log.get_report()[0]["input_tokens"] == {
        "state": "measured",
        "value": 2,
    }
    assert flushed[0]["telemetry"].token_usage["input_tokens"] == {"state": "measured", "value": 2}


@pytest.mark.anyio
async def test_otlp_tokens_captured_before_runner_crash_reach_terminal_artifact(
    minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.execution.headless._headless_execute as execute_module
    from autoskillit.execution.headless import _execute_claude_headless
    from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
    from tests.execution.conftest import _launch_preparation, _mock_backend

    selected = {
        "backend": "claude-code",
        "provider_used": "anthropic",
        "input_tokens": {"state": "measured_zero", "value": 0},
        "output_tokens": {"state": "measured", "value": 3},
        "cache_read_tokens": {"state": "measured_zero", "value": 0},
        "cache_write_tokens": {"state": "measured", "value": 7},
        "peak_context": {"state": "measured_zero", "value": 0},
    }
    events: list[str] = []
    _install_fake_sink(
        monkeypatch,
        execute_module,
        events,
        minimal_ctx.config.linux_tracing.log_dir,
        close_raises=False,
        token_evidence={"native-before-crash": selected},
    )
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

    async def crashing_runner(_cmd, **kwargs):
        callback = kwargs.get("on_session_id_resolved")
        if callback is not None:
            callback("native-before-crash")
        raise RuntimeError("runner crashed")

    minimal_ctx.runner = crashing_runner  # type: ignore[assignment]
    flushed: list[dict[str, object]] = []
    import autoskillit.execution.evidence.session_log as _sl_mod

    monkeypatch.setattr(_sl_mod, "flush_session_log", lambda **kwargs: flushed.append(kwargs))

    result = await _execute_claude_headless(
        lambda _binding, extras: ClaudeHeadlessCmd(
            cmd=("claude", "-p", "test"), env=dict(extras or {})
        ),
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        step_name="sink-crash-tokens",
        launch_resolver=minimal_ctx.launch_resolver,
        launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
    )

    assert result.subtype == "crashed"
    assert result.token_usage == selected
    assert flushed[0]["telemetry"].token_usage == selected


@pytest.mark.anyio
async def test_sink_close_failure_does_not_replace_runner_crash(
    minimal_ctx, tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution as _execution
    import autoskillit.execution.headless._headless_execute as _execute_module
    from autoskillit.execution.headless import _execute_claude_headless
    from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
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
    assert events == ["start", "close", "lookup:", "flush", "close"]
    assert {key: runner_envs[0][key] for key in sink_env} == sink_env
    assert os.environ == parent_environment


@pytest.mark.anyio
async def test_sink_close_failure_does_not_replace_propagated_infrastructure_fault(
    minimal_ctx, tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution.headless._headless_execute as _execute_module
    from autoskillit.core import InfrastructureFaultError, RetryReason, SkillResult
    from autoskillit.execution.headless import _execute_claude_headless
    from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
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
    from autoskillit.execution.headless import _execute_claude_headless
    from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
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

    assert events == ["start", "close", "lookup:", "flush", "close"]
    assert {key: runner_envs[0][key] for key in sink_env} == sink_env
    assert os.environ == parent_environment


async def test_drain_model_evidence_falls_back_to_captured_session_id() -> None:
    """Empty terminal session id must fall back to captured native id in evidence lookup."""
    from autoskillit.core import ModelIdentity
    from autoskillit.execution.headless._headless_model_evidence import _drain_model_evidence

    lookups: list[str] = []

    class _StubSink:
        def close(self) -> None:
            return None

        def model_evidence_for(self, session_id: str):
            lookups.append(f"model:{session_id}")
            return "captured-model-id", ()

        def token_usage_for(self, session_id: str, backend: str, provider_used: str):
            lookups.append(f"tokens:{session_id}:{backend}:{provider_used}")
            return {"backend": backend, "provider_used": provider_used}

    captured_session = "native-only-session-42"
    evidence_session_id, _, _, usage = _drain_model_evidence(
        _StubSink(),
        terminal_session_id="",
        captured_session_id=captured_session,
        model_identity=ModelIdentity(
            configured_model="opus",
            effective_model="",
            profile_name="",
        ),
        backend="claude-code",
        provider_used="anthropic",
    )

    assert evidence_session_id == captured_session
    assert usage == {"backend": "claude-code", "provider_used": "anthropic"}
    assert lookups == [
        f"model:{captured_session}",
        f"tokens:{captured_session}:claude-code:anthropic",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("raw_session_type", "expected_session_type"),
    [("skill", "skill"), ("", None)],
)
async def test_managed_session_type_reaches_runner_and_session_index(
    minimal_ctx,
    tmp_path: Path,
    raw_session_type: str,
    expected_session_type: str | None,
) -> None:
    from autoskillit.execution.headless import run_headless_core
    from tests.execution.conftest import _mock_backend
    from tests.fakes import MockSubprocessRunner

    result = SubprocessResult(
        returncode=0,
        stdout=json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "sess-session-type-test",
            }
        ),
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
        proc_snapshots=[],
    )
    runner = MockSubprocessRunner()
    runner.set_default(result)
    minimal_ctx.runner = runner
    minimal_ctx.config.linux_tracing.log_dir = str(tmp_path)
    backend = _mock_backend(pty_required=True, channel_b_capable=True)
    backend.build_skill_session_cmd.return_value = CmdSpec(
        cmd=("claude", "-p", "test"),
        env={"AUTOSKILLIT_SESSION_TYPE": raw_session_type},
    )
    minimal_ctx.backend = backend

    await run_headless_core("/test foo", str(tmp_path), minimal_ctx)

    _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
    assert kwargs["env"]["AUTOSKILLIT_SESSION_TYPE"] == raw_session_type
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["session_type"] == expected_session_type


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
            patch.object(
                _patch_headless__headless_execute,
                "is_git_main_checkout",
                return_value=True,
            ),
            patch.object(
                _patch_headless__headless_execute,
                "validate_pre_session_index",
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
            patch.object(
                _patch_headless__headless_execute,
                "is_git_main_checkout",
                return_value=True,
            ),
            patch.object(
                _patch_headless__headless_execute,
                "validate_pre_session_index",
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
