"""Group G (execution part): AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT env variable injection tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import CmdSpec
from autoskillit.core.types import SubprocessResult, TerminationReason
from tests.execution.conftest import _mock_backend
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _success_result() -> SubprocessResult:
    """Build a minimal successful SubprocessResult for MockSubprocessRunner."""
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


class TestExecuteClaudeHeadlessIdleEnv:
    @pytest.mark.anyio
    async def test_execute_claude_headless_ignores_ambient_idle_output_env(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        """Ambient AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT is a child hint, not parent liveness."""
        from autoskillit.execution.headless import run_headless_core

        monkeypatch.setenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", "30")
        minimal_ctx.config.run_skill.idle_output_timeout = 45
        minimal_ctx.runner = MockSubprocessRunner()
        minimal_ctx.runner.set_default(_success_result())
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        await run_headless_core("/investigate foo", str(tmp_path), minimal_ctx)

        assert minimal_ctx.runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = minimal_ctx.runner.call_args_list[0]
        assert kwargs.get("idle_output_timeout") == 45.0, (
            f"Expected idle_output_timeout=45.0, got {kwargs.get('idle_output_timeout')!r}"
        )

    @pytest.mark.anyio
    async def test_idle_output_timeout_priority_chain(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        """Priority chain: per-step arg > resolved config; ambient env is ignored.

        Level 1: per-step arg beats env and cfg.
        Level 2: cfg is used when per-step arg is None, even if ambient env exists.
        Level 3: cfg is used when both arg and env are absent.
        """
        from autoskillit.execution.headless import run_headless_core

        # Level 1: per-step arg takes priority over env and cfg
        monkeypatch.setenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", "30")
        minimal_ctx.config.run_skill.idle_output_timeout = 60
        minimal_ctx.runner = MockSubprocessRunner()
        minimal_ctx.runner.set_default(_success_result())
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        await run_headless_core(
            "/investigate foo", str(tmp_path), minimal_ctx, idle_output_timeout=15.0
        )
        _, _, _, kwargs1 = minimal_ctx.runner.call_args_list[0]
        assert kwargs1.get("idle_output_timeout") == 15.0, (
            f"Level 1 (per-step arg): expected 15.0, got {kwargs1.get('idle_output_timeout')!r}"
        )

        # Level 2: cfg wins when per-step arg is None; ambient env is a child hint only.
        minimal_ctx.runner = MockSubprocessRunner()
        minimal_ctx.runner.set_default(_success_result())

        await run_headless_core(
            "/investigate foo", str(tmp_path), minimal_ctx, idle_output_timeout=None
        )
        _, _, _, kwargs2 = minimal_ctx.runner.call_args_list[0]
        assert kwargs2.get("idle_output_timeout") == 60.0, (
            f"Level 2 (cfg): expected 60.0, got {kwargs2.get('idle_output_timeout')!r}"
        )

        # Level 3: cfg when env is absent and arg is None
        monkeypatch.delenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT")
        minimal_ctx.config.run_skill.idle_output_timeout = 45
        minimal_ctx.runner = MockSubprocessRunner()
        minimal_ctx.runner.set_default(_success_result())

        await run_headless_core(
            "/investigate foo", str(tmp_path), minimal_ctx, idle_output_timeout=None
        )
        _, _, _, kwargs3 = minimal_ctx.runner.call_args_list[0]
        assert kwargs3.get("idle_output_timeout") == 45.0, (
            f"Level 3 (cfg): expected 45.0, got {kwargs3.get('idle_output_timeout')!r}"
        )

    @pytest.mark.anyio
    async def test_idle_output_env_zero_does_not_disable_parent_watchdog(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        """Ambient AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT=0 does not override resolved config."""
        from autoskillit.execution.headless import run_headless_core

        monkeypatch.setenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", "0")
        minimal_ctx.config.run_skill.idle_output_timeout = 45
        minimal_ctx.runner = MockSubprocessRunner()
        minimal_ctx.runner.set_default(_success_result())
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        await run_headless_core("/investigate foo", str(tmp_path), minimal_ctx)

        assert minimal_ctx.runner.call_args_list, "runner was never called"
        _, _, _, kwargs = minimal_ctx.runner.call_args_list[0]
        actual = kwargs.get("idle_output_timeout")
        assert actual == 45.0, f"Expected idle_output_timeout=45.0 when env=0, got {actual!r}"

    @pytest.mark.anyio
    async def test_idle_output_env_invalid_float_falls_back_to_config(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.execution.headless import run_headless_core

        monkeypatch.setenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", "not-a-number")

        async def _no_nudge(*_a, **_kw):
            return None

        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_execute._attempt_contract_nudge",
            _no_nudge,
        )
        minimal_ctx.config.run_skill.idle_output_timeout = 45

        runner = MockSubprocessRunner()
        runner.set_default(_success_result())
        minimal_ctx.runner = runner
        minimal_ctx.backend = _mock_backend()

        await run_headless_core(
            "/autoskillit:some-skill",
            str(tmp_path),
            minimal_ctx,
        )
        idle = runner.call_args_list[-1][3].get("idle_output_timeout")
        assert idle == 45.0

    @pytest.mark.anyio
    async def test_resolved_idle_output_timeout_rewrites_child_env(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.execution.headless import run_headless_core

        monkeypatch.delenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", raising=False)
        minimal_ctx.config.run_skill.idle_output_timeout = 45
        runner = MockSubprocessRunner()
        runner.set_default(_success_result())
        minimal_ctx.runner = runner
        backend = _mock_backend(pty_required=True, channel_b_capable=True)
        backend.build_skill_session_cmd.return_value = CmdSpec(
            cmd=("claude", "--print", "test-skill"),
            env={"AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": "3.0", "KEEP": "1"},
        )
        minimal_ctx.backend = backend

        await run_headless_core("/investigate foo", str(tmp_path), minimal_ctx)

        env = runner.call_args_list[-1][3].get("env")
        assert isinstance(env, dict)
        assert env["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] == "45.0"
        assert env["KEEP"] == "1"

    @pytest.mark.anyio
    async def test_explicit_zero_idle_output_timeout_removes_child_env(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.execution.headless import run_headless_core

        monkeypatch.delenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", raising=False)
        minimal_ctx.config.run_skill.idle_output_timeout = 45
        runner = MockSubprocessRunner()
        runner.set_default(_success_result())
        minimal_ctx.runner = runner
        backend = _mock_backend(pty_required=True, channel_b_capable=True)
        backend.build_skill_session_cmd.return_value = CmdSpec(
            cmd=("claude", "--print", "test-skill"),
            env={"AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": "3.0", "KEEP": "1"},
        )
        minimal_ctx.backend = backend

        await run_headless_core(
            "/investigate foo",
            str(tmp_path),
            minimal_ctx,
            idle_output_timeout=0.0,
        )

        env = runner.call_args_list[-1][3].get("env")
        assert isinstance(env, dict)
        assert "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT" not in env
        assert env["KEEP"] == "1"
        assert runner.call_args_list[-1][3].get("idle_output_timeout") is None


class TestDispatchFoodTruckIdleEnvInjection:
    @pytest.mark.anyio
    async def test_dispatch_food_truck_injects_idle_output_timeout_env(
        self, minimal_ctx, tmp_path: Path, monkeypatch
    ) -> None:
        """dispatch_food_truck adds AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT to env_extras
        based on fleet config idle_output_timeout (priority: caller > fleet > run_skill)."""
        from dataclasses import replace
        from unittest.mock import Mock

        from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec
        from autoskillit.core.types import KillReason, RetryReason
        from autoskillit.core.types import SkillResult as _SkillResult
        from autoskillit.core.types._type_plugin_source import DirectInstall
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        minimal_ctx.config.fleet.idle_output_timeout = 120

        backend = Mock()
        backend.name = "claude-code"
        backend.capabilities = replace(CLAUDE_CODE_CAPABILITIES)
        backend.build_food_truck_cmd.return_value = CmdSpec(
            cmd=("claude", "--print", "test"), env={}
        )
        backend.write_tool_names.return_value = frozenset({"Write", "Edit"})
        minimal_ctx.backend = backend

        async def _fake_execute(*_args: Any, **_kwargs: Any) -> _SkillResult:
            return _SkillResult(
                success=True,
                result="done",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
                kill_reason=KillReason.NATURAL_EXIT,
            )

        monkeypatch.setattr(
            "autoskillit.execution.headless._execute_claude_headless",
            _fake_execute,
        )

        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path / "plugin")
        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            orchestrator_prompt="test",
            cwd=str(tmp_path),
            completion_marker="%%DONE%%",
            env_extras=None,
        )

        backend.build_food_truck_cmd.assert_called_once()
        call_kwargs = backend.build_food_truck_cmd.call_args[1]
        env = call_kwargs.get("env_extras") or {}
        assert "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT" in env, (
            f"Expected AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT in env_extras, got {env!r}"
        )
        assert env["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] == "120"
