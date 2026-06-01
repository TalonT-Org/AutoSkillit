"""End-to-end tests verifying run_headless_core routes command construction through ctx.backend."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.core.types import RetryReason, SkillResult, SubprocessResult, TerminationReason

from .conftest import _mock_backend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.anyio
async def test_run_headless_core_uses_ctx_backend_for_command_construction(minimal_ctx):
    backend = _mock_backend()
    minimal_ctx.backend = backend

    mock_runner = AsyncMock()
    mock_result = SubprocessResult(
        returncode=0,
        stdout="",
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )
    mock_runner.return_value = mock_result
    minimal_ctx.runner = mock_runner

    with patch(
        "autoskillit.execution.headless._headless_execute._build_skill_result"
    ) as mock_build_result:
        mock_build_result.return_value = SkillResult(
            success=True,
            result="",
            session_id="test-session",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )

        from autoskillit.execution.headless import run_headless_core

        await run_headless_core(
            "/autoskillit:test-skill",
            "/tmp/test-cwd",
            minimal_ctx,
            completion_marker="%%DONE%%",
        )

    backend.build_skill_session_cmd.assert_called_once()
    call_args = backend.build_skill_session_cmd.call_args
    assert call_args.args[0] == "/autoskillit:test-skill"
    assert call_args.args[1] == "/tmp/test-cwd"
    config = call_args.args[2]
    assert config.completion_marker == "%%DONE%%"


class TestBackendDispatchRouting:
    @pytest.mark.anyio
    async def test_codex_backend_pty_mode_false_reaches_runner(self, minimal_ctx):
        backend = _mock_backend(pty_required=False, channel_b_capable=False)
        minimal_ctx.backend = backend

        mock_runner = AsyncMock()
        mock_result = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )
        mock_runner.return_value = mock_result
        minimal_ctx.runner = mock_runner

        with patch(
            "autoskillit.execution.headless._headless_execute._build_skill_result"
        ) as mock_build_result:
            mock_build_result.return_value = SkillResult(
                success=True,
                result="",
                session_id="test-session",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )

            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test-skill",
                "/tmp/test-cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
            )

        assert mock_runner.call_args.kwargs["pty_mode"] is False


class TestStepBackendOverride:
    @pytest.mark.anyio
    async def test_step_backend_none_falls_back_to_ctx_backend(self, minimal_ctx):
        backend = _mock_backend()
        minimal_ctx.backend = backend
        minimal_ctx.runner = AsyncMock(
            return_value=SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )
        with patch(
            "autoskillit.execution.headless._headless_execute._build_skill_result"
        ) as mock_build:
            mock_build.return_value = SkillResult(
                success=True,
                result="",
                session_id="s",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
            )
        assert mock_build.call_args.kwargs["backend"] is backend

    @pytest.mark.anyio
    async def test_step_backend_override_routes_through_get_backend(self, minimal_ctx):
        backend = _mock_backend()
        minimal_ctx.backend = backend
        minimal_ctx.runner = AsyncMock(
            return_value=SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )
        codex_backend = _mock_backend(
            pty_required=False, channel_b_capable=False, process_name="codex"
        )
        codex_backend.name = "codex"
        with (
            patch(
                "autoskillit.execution.headless.get_backend", return_value=codex_backend
            ) as mock_get,
            patch("autoskillit.execution.headless._execute_claude_headless") as mock_exec,
        ):
            mock_exec.return_value = SkillResult(
                success=True,
                result="",
                session_id="s",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
                backend_override="codex",
            )
            mock_get.assert_called_once_with("codex")
            assert mock_exec.call_args.kwargs["step_backend"] is codex_backend

    @pytest.mark.anyio
    async def test_step_backend_flows_to_stream_parser_and_build_result(self, minimal_ctx):
        ctx_backend = _mock_backend()
        step_backend_mock = _mock_backend(
            pty_required=False, channel_b_capable=False, process_name="codex"
        )
        step_backend_mock.name = "codex"
        minimal_ctx.backend = ctx_backend
        minimal_ctx.runner = AsyncMock(
            return_value=SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )
        with patch(
            "autoskillit.execution.headless._headless_execute._build_skill_result"
        ) as mock_build:
            mock_build.return_value = SkillResult(
                success=True,
                result="",
                session_id="s",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
            from autoskillit.core.types import CmdSpec
            from autoskillit.execution.headless._headless_execute import _execute_claude_headless

            await _execute_claude_headless(
                CmdSpec(cmd=("codex", "--quiet", "test"), env={}),
                "/tmp/cwd",
                minimal_ctx,
                timeout=60.0,
                stale_threshold=30.0,
                completion_marker="%%DONE%%",
                step_backend=step_backend_mock,
            )
        step_backend_mock.stream_parser.assert_called_once()
        ctx_backend.stream_parser.assert_not_called()
        assert mock_build.call_args.kwargs["backend"] is step_backend_mock

    @pytest.mark.anyio
    async def test_backend_override_unknown_name_raises_valueerror(self, minimal_ctx):
        backend = _mock_backend()
        minimal_ctx.backend = backend
        minimal_ctx.runner = AsyncMock()
        from autoskillit.execution.headless import run_headless_core

        with pytest.raises(ValueError, match="Unknown backend"):
            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
                backend_override="nonexistent",
            )

    def test_resolve_pty_mode_accepts_backend_directly(self):
        backend = _mock_backend(pty_required=False)
        from autoskillit.execution.headless._headless_helpers import _resolve_pty_mode

        assert _resolve_pty_mode(backend) is False

    def test_resolve_session_log_dir_accepts_backend_directly(self, monkeypatch):
        backend = _mock_backend(channel_b_capable=False)
        from autoskillit.execution.headless._headless_helpers import _resolve_session_log_dir

        assert _resolve_session_log_dir("/tmp/cwd", backend) is None

    @pytest.mark.anyio
    async def test_protocol_accepts_backend_override(self):
        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        await executor.run("/test", "/tmp", backend_override="codex")
        assert executor.calls[0].backend_override == "codex"


def _patch_for_flush(monkeypatch, tmp_path, skill_result):
    """Monkeypatch internals so _execute_claude_headless reaches flush_session_log.

    Mirrors _patch_common from test_flush_provider_integration.py, minus the ctx
    argument and the unused _sub_result shared state.
    """
    import autoskillit.execution.session_log as _sl_mod
    from autoskillit.execution.headless import PostSessionMetrics

    sub_result = SubprocessResult(
        returncode=1,
        stdout="",
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=99,
    )

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        return sub_result

    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *a, **kw: skill_result,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha",
        lambda *a: "",  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute.collect_version_snapshot",
        lambda backend=None: {},
    )

    flush_calls: list[dict] = []
    monkeypatch.setattr(_sl_mod, "flush_session_log", lambda **kw: flush_calls.append(kw))
    return fake_runner, flush_calls


class TestCodexLogDispatch:
    """Verify channel_b_capable drives codex log dispatch to flush_session_log."""

    @pytest.mark.anyio
    async def test_channel_b_false_dispatches_codex_log_via_locate_session(
        self, minimal_ctx, tmp_path, monkeypatch
    ):
        from autoskillit.core.types import CmdSpec
        from autoskillit.execution.headless._headless_execute import _execute_claude_headless

        result = SkillResult(
            success=False,
            result="",
            session_id="sid-1",
            subtype="error",
            is_error=True,
            exit_code=1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        backend = _mock_backend(channel_b_capable=False, process_name="codex")
        backend.name = "codex"
        sentinel = Path("/sentinel/codex.jsonl")
        backend.session_locator().locate_session.return_value = sentinel

        fake_runner, flush_calls = _patch_for_flush(monkeypatch, tmp_path, result)
        minimal_ctx.runner = fake_runner  # type: ignore[assignment]
        minimal_ctx.backend = _mock_backend()

        await _execute_claude_headless(
            CmdSpec(cmd=("codex", "--quiet", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            step_backend=backend,
        )

        backend.session_locator().locate_session.assert_called_once_with("sid-1")
        assert flush_calls[0]["codex_log_path"] == sentinel

    @pytest.mark.anyio
    async def test_channel_b_true_skips_session_locator(self, minimal_ctx, tmp_path, monkeypatch):
        from autoskillit.core.types import CmdSpec
        from autoskillit.execution.headless._headless_execute import _execute_claude_headless

        result = SkillResult(
            success=False,
            result="",
            session_id="sid-2",
            subtype="error",
            is_error=True,
            exit_code=1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        backend = _mock_backend(channel_b_capable=True)
        fake_runner, flush_calls = _patch_for_flush(monkeypatch, tmp_path, result)
        minimal_ctx.runner = fake_runner  # type: ignore[assignment]
        minimal_ctx.backend = backend

        await _execute_claude_headless(
            CmdSpec(cmd=("claude", "--print", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            step_backend=backend,
        )

        backend.session_locator.assert_not_called()
        assert flush_calls[0]["codex_log_path"] is None
