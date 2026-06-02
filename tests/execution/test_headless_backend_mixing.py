"""Integration tests for per-step backend mixing in the execution layer.

Verifies that when backend_override='claude-code' is active, the Codex ctx.backend
is bypassed and ClaudeCodeBackend handles command construction and env policy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.core.types import (
    CmdSpec,
    RetryReason,
    SkillResult,
    SubprocessResult,
    TerminationReason,
)

from .conftest import _mock_backend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _stub_result() -> SkillResult:
    return SkillResult(
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


class TestBackendMixingCommandRouting:
    """Codex ctx.backend bypassed when backend_override='claude-code'."""

    @pytest.mark.anyio
    async def test_codex_ctx_backend_not_called_when_override_claude_code(self, minimal_ctx):
        codex_backend = _mock_backend(pty_required=False, channel_b_capable=False)
        codex_backend.name = "codex"
        minimal_ctx.backend = codex_backend
        minimal_ctx.runner = AsyncMock(
            return_value=SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )

        claude_code_backend = _mock_backend(pty_required=True, channel_b_capable=True)
        claude_code_backend.name = "claude-code"

        with (
            patch(
                "autoskillit.execution.headless.get_backend",
                return_value=claude_code_backend,
            ),
            patch(
                "autoskillit.execution.headless._execute_claude_headless",
            ) as mock_exec,
        ):
            mock_exec.return_value = _stub_result()
            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
                backend_override="claude-code",
            )

            claude_code_backend.build_skill_session_cmd.assert_called_once()
            codex_backend.build_skill_session_cmd.assert_not_called()


class TestBackendMixingEnvPolicy:
    """CmdSpec.env from override backend contains ANTHROPIC_BASE_URL."""

    @pytest.mark.anyio
    async def test_override_cmd_spec_env_contains_anthropic_base_url(self, minimal_ctx):
        codex_backend = _mock_backend(pty_required=False, channel_b_capable=False)
        codex_backend.name = "codex"
        minimal_ctx.backend = codex_backend

        claude_code_backend = _mock_backend(pty_required=True, channel_b_capable=True)
        claude_code_backend.name = "claude-code"
        claude_code_backend.build_skill_session_cmd.return_value = CmdSpec(
            cmd=("claude", "--print", "test-skill"),
            env={
                "ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1/anthropic",
                "ANTHROPIC_API_KEY": "minimax-key",
                "AUTOSKILLIT_HEADLESS": "1",
            },
        )

        minimal_ctx.runner = AsyncMock(
            return_value=SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )

        captured_spec = None

        async def capture_exec(spec, *args, **kwargs):
            nonlocal captured_spec
            captured_spec = spec
            return _stub_result()

        with (
            patch(
                "autoskillit.execution.headless.get_backend",
                return_value=claude_code_backend,
            ),
            patch(
                "autoskillit.execution.headless._execute_claude_headless",
                side_effect=capture_exec,
            ),
        ):
            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
                backend_override="claude-code",
            )

        assert captured_spec is not None
        assert captured_spec.env["ANTHROPIC_BASE_URL"] == ("https://api.minimax.chat/v1/anthropic")


class TestDefaultExecutorBackendMixing:
    """DefaultHeadlessExecutor.run() end-to-end with backend_override."""

    @pytest.mark.anyio
    async def test_default_executor_end_to_end_backend_override(self, minimal_ctx):
        codex_backend = _mock_backend(pty_required=False, channel_b_capable=False)
        codex_backend.name = "codex"
        minimal_ctx.backend = codex_backend
        minimal_ctx.runner = AsyncMock(
            return_value=SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )

        claude_code_backend = _mock_backend(pty_required=True, channel_b_capable=True)
        claude_code_backend.name = "claude-code"

        with (
            patch(
                "autoskillit.execution.headless.get_backend",
                return_value=claude_code_backend,
            ) as mock_get_backend,
            patch(
                "autoskillit.execution.headless._execute_claude_headless",
            ) as mock_exec,
        ):
            mock_exec.return_value = _stub_result()
            from autoskillit.execution.headless import DefaultHeadlessExecutor

            executor = DefaultHeadlessExecutor(minimal_ctx)
            await executor.run(
                "/autoskillit:test",
                "/tmp/cwd",
                backend_override="claude-code",
            )

            mock_get_backend.assert_called_once_with("claude-code")
            claude_code_backend.build_skill_session_cmd.assert_called_once()
            codex_backend.build_skill_session_cmd.assert_not_called()
