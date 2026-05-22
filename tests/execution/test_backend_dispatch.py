"""End-to-end tests verifying run_headless_core routes command construction through ctx.backend."""

from __future__ import annotations

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
    call_kwargs = backend.build_skill_session_cmd.call_args
    assert call_kwargs.args[0] == "/autoskillit:test-skill"
    assert call_kwargs.kwargs["cwd"] == "/tmp/test-cwd"
    assert call_kwargs.kwargs["completion_marker"] == "%%DONE%%"
