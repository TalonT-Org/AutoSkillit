"""End-to-end tests verifying run_headless_core routes command construction through ctx.backend."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec
from autoskillit.core.types import RetryReason, SkillResult, SubprocessResult, TerminationReason

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _mock_backend(
    *,
    pty_required: bool = True,
    channel_b_capable: bool = True,
) -> Mock:
    caps = replace(
        CLAUDE_CODE_CAPABILITIES,
        pty_required=pty_required,
        channel_b_capable=channel_b_capable,
    )
    backend = Mock()
    backend.name = "claude-code"
    backend.capabilities = caps
    backend.build_skill_session_cmd.return_value = CmdSpec(
        cmd=("claude", "--print", "test-prompt"),
        env={"AUTOSKILLIT_HEADLESS": "1"},
    )
    backend.build_resume_cmd.return_value = CmdSpec(
        cmd=("claude", "--print", "emit marker", "--resume", "test-session"),
        env={},
    )
    backend.write_tool_names.return_value = frozenset({"Write", "Edit"})
    backend.result_parser.return_value = Mock()
    return backend


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

    with patch("autoskillit.execution.headless._build_skill_result") as mock_build_result:
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
