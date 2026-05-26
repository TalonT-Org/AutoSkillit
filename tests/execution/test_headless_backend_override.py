"""Tests verifying run_headless_core delegates to backend from backend_override."""

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


class TestBackendOverrideCommandRouting:
    """Verify build_skill_session_cmd is called on the override backend, not ctx.backend."""

    @pytest.mark.anyio
    async def test_backend_override_routes_to_claude_code_backend(self, minimal_ctx):
        """When backend_override=claude-code, the claude-code mock's cmd builder is called."""
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

        claude_code_backend = _mock_backend()
        claude_code_backend.name = "claude-code"

        with (
            patch("autoskillit.execution.headless.get_backend", return_value=claude_code_backend),
            patch("autoskillit.execution.headless._execute_claude_headless") as mock_exec,
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

    @pytest.mark.anyio
    async def test_backend_override_none_uses_ctx_backend(self, minimal_ctx):
        """When backend_override=None, ctx.backend's build_skill_session_cmd is called."""
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
            mock_build.return_value = _stub_result()
            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
            )

            backend.build_skill_session_cmd.assert_called_once()

    @pytest.mark.anyio
    async def test_backend_override_codex_uses_codex_backend(self, minimal_ctx):
        """When backend_override='codex', codex mock's build_skill_session_cmd is called."""
        claude_code_backend = _mock_backend()
        claude_code_backend.name = "claude-code"
        minimal_ctx.backend = claude_code_backend
        minimal_ctx.runner = AsyncMock(
            return_value=SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )

        codex_backend = _mock_backend(pty_required=False, channel_b_capable=False)
        codex_backend.name = "codex"

        with (
            patch("autoskillit.execution.headless.get_backend", return_value=codex_backend),
            patch("autoskillit.execution.headless._execute_claude_headless") as mock_exec,
        ):
            mock_exec.return_value = _stub_result()
            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
                backend_override="codex",
            )

            codex_backend.build_skill_session_cmd.assert_called_once()
            claude_code_backend.build_skill_session_cmd.assert_not_called()


class TestBackendOverrideEnvPolicy:
    """Tests verifying spec.env reflects the override backend's env policy."""

    @pytest.mark.anyio
    async def test_claude_code_step_backend_includes_anthropic_key(self, minimal_ctx, monkeypatch):
        """Claude-code override backend includes ANTHROPIC_API_KEY in spec.env."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        codex_backend = _mock_backend(pty_required=False, channel_b_capable=False)
        codex_backend.name = "codex"
        minimal_ctx.backend = codex_backend

        claude_code_backend = _mock_backend()
        claude_code_backend.name = "claude-code"
        claude_code_backend.build_skill_session_cmd.return_value = CmdSpec(
            cmd=("claude", "--print", "test-skill"),
            env={"ANTHROPIC_API_KEY": "test-key", "AUTOSKILLIT_HEADLESS": "1"},
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
            patch("autoskillit.execution.headless.get_backend", return_value=claude_code_backend),
            patch(
                "autoskillit.execution.headless._execute_claude_headless", side_effect=capture_exec
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
        assert captured_spec.env.get("ANTHROPIC_API_KEY") == "test-key"

    @pytest.mark.anyio
    async def test_codex_step_backend_strips_anthropic_key(self, minimal_ctx, monkeypatch):
        """Codex override backend strips ANTHROPIC_API_KEY from spec.env."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        claude_code_backend = _mock_backend()
        claude_code_backend.name = "claude-code"
        minimal_ctx.backend = claude_code_backend

        codex_backend = _mock_backend(pty_required=False, channel_b_capable=False)
        codex_backend.name = "codex"
        codex_backend.build_skill_session_cmd.return_value = CmdSpec(
            cmd=("codex", "--quiet", "test-skill"),
            env={"AUTOSKILLIT_HEADLESS": "1"},
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
            patch("autoskillit.execution.headless.get_backend", return_value=codex_backend),
            patch(
                "autoskillit.execution.headless._execute_claude_headless", side_effect=capture_exec
            ),
        ):
            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
                backend_override="codex",
            )

        assert captured_spec is not None
        assert "ANTHROPIC_API_KEY" not in captured_spec.env


class TestBackendOverrideParserSelection:
    """Tests verifying stream_parser is called on the override backend, not ctx.backend."""

    @pytest.mark.anyio
    async def test_step_backend_parser_used_not_ctx_backend(self, minimal_ctx):
        """When backend_override is set, step_backend.stream_parser is called."""
        ctx_backend = _mock_backend(pty_required=False, channel_b_capable=False)
        ctx_backend.name = "codex"
        step_backend = _mock_backend()
        step_backend.name = "claude-code"
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

        with (
            patch("autoskillit.execution.headless.get_backend", return_value=step_backend),
            patch(
                "autoskillit.execution.headless._headless_execute._build_skill_result"
            ) as mock_build,
        ):
            mock_build.return_value = _stub_result()
            from autoskillit.execution.headless import run_headless_core

            await run_headless_core(
                "/autoskillit:test",
                "/tmp/cwd",
                minimal_ctx,
                completion_marker="%%DONE%%",
                backend_override="claude-code",
            )

            step_backend.stream_parser.assert_called_once()
            ctx_backend.stream_parser.assert_not_called()
